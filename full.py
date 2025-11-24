import multiprocessing
import serial
import time
import struct
import os
import sys
import json
import queue
import numpy as np
import serial.tools.list_ports as list_ports
import pyaudio

# --- CONFIGURATION ---
SESSION_NAME = f"multimodal_capture_{int(time.time())}"
OUTPUT_DIR = os.path.join(os.getcwd(), SESSION_NAME)

# Hardware/Speed Settings
CSI_PORT = "COM5"
CSI_BAUD = 2000000
AUDIO_DEVICE = 1
SAMPLE_RATE = 44100
CHANNELS = 1
FORMAT = pyaudio.paInt16
DTYPE_NPY = 'int16'
CHUNK_SIZE_SAMPLES = 1024

# Efficiency Settings
FILE_SIZE_LIMIT = 1024 * 1024 * 500  # 500 MB per file
CSI_READ_SIZE = 4096 * 8              # Bulk read like optimized script
CSI_WRITE_THRESHOLD = 1024 * 128      # Write every 128KB

# ESP32 CSI Packet Structure
MAGIC_BYTE_SEQ = b'\xFA\xFA'
# ESP32 Header: Magic(2) | Len(2) | RSSI(1) | Chan(1) | Time(4) = 12 bytes + payload

# Audio Headers (for binary log)
AUDIO_HEADER_STRUCT = struct.Struct('<2sdI')
MAGIC_AUDIO = b'\xAA\xAA'

# ---------------------------------------------------------
# PROCESS 1: AUDIO ENGINE (PyAudio Callback)
# ---------------------------------------------------------
audio_queue = multiprocessing.Queue()

def pyaudio_callback(in_data, frame_count, time_info, status):
    """
    Called by PyAudio driver thread.
    Convert raw bytes to NumPy array before putting in queue.
    """
    audio_array = np.frombuffer(in_data, dtype=DTYPE_NPY)
    audio_queue.put(audio_array)
    return (None, pyaudio.paContinue)

def audio_worker(stop_event, output_dir, stats_queue):
    p = pyaudio.PyAudio()
    stream = None
    
    # Binary File Manager
    def get_file_manager(prefix, output_dir, limit):
        file_idx = 0
        current_size = 0
        f_out = None

        def open_new():
            nonlocal file_idx, current_size, f_out
            if f_out:
                f_out.close()
            fname = os.path.join(output_dir, f"{prefix}_part_{file_idx:03d}.bin")
            f_out = open(fname, 'wb')
            file_idx += 1
            current_size = 0
            return f_out, file_idx - 1

        f_out, _ = open_new()

        def write_and_check(data, size_to_add):
            nonlocal f_out, current_size, file_idx
            f_out.write(data)
            current_size += size_to_add
            
            if current_size >= limit:
                f_out.close()
                f_out, file_idx = open_new()
                return True, file_idx
            return False, file_idx

        return write_and_check, f_out

    audio_manager, f_out = get_file_manager('audio', output_dir, FILE_SIZE_LIMIT)
    last_stat_time = time.time()
    
    try:
        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=SAMPLE_RATE,
                        input=True,
                        frames_per_buffer=CHUNK_SIZE_SAMPLES,
                        input_device_index=AUDIO_DEVICE,
                        stream_callback=pyaudio_callback)
        
        print(f"[AUDIO] PyAudio stream opened on device {AUDIO_DEVICE}")
        
        while not stop_event.is_set():
            try:
                data_array = audio_queue.get(timeout=0.1)
                
                # Prepare header
                t_now = time.time()
                chunk_len = len(data_array) * data_array.itemsize
                header = AUDIO_HEADER_STRUCT.pack(MAGIC_AUDIO, t_now, chunk_len)
                
                # Write header
                audio_manager(header, len(header))
                
                # Write audio data as bytes
                audio_bytes = data_array.tobytes()
                rotated, clip_idx = audio_manager(audio_bytes, len(audio_bytes))
                
                # Send stats
                if time.time() - last_stat_time > 0.5:
                    stats_queue.put({
                        "type": "audio",
                        "clip": clip_idx,
                        "size_mb": os.path.getsize(f_out.name) / (1024*1024),
                    })
                    last_stat_time = time.time()
                
            except queue.Empty:
                continue
            
    except Exception as e:
        stats_queue.put({"type": "error", "msg": f"Audio Crash: {e}"})
    finally:
        if stream:
            stream.close()
        p.terminate()
        if f_out:
            f_out.close()

# ---------------------------------------------------------
# PROCESS 2: CSI ENGINE (Optimized Packet Parser)
# ---------------------------------------------------------
def csi_worker(stop_event, output_dir, stats_queue, port):
    # File Manager
    def get_file_manager(prefix, output_dir, limit):
        file_idx = 0
        current_size = 0
        f_out = None

        def open_new():
            nonlocal file_idx, current_size, f_out
            if f_out:
                f_out.close()
            fname = os.path.join(output_dir, f"{prefix}_part_{file_idx:03d}.bin")
            f_out = open(fname, 'wb')
            file_idx += 1
            current_size = 0
            return f_out, file_idx - 1

        f_out, _ = open_new()

        def write_and_check(data, size_to_add):
            nonlocal f_out, current_size, file_idx
            f_out.write(data)
            current_size += size_to_add
            
            if current_size >= limit:
                f_out.close()
                f_out, file_idx = open_new()
                return True, file_idx
            return False, file_idx

        return write_and_check, f_out

    csi_manager, f_out = get_file_manager('csi', output_dir, FILE_SIZE_LIMIT)
    
    packet_count = 0
    last_stat_time = time.time()
    ser = None
    
    # Parser state
    buffer = bytearray()
    chunk_buffer = bytearray()  # For batched writes

    try:
        ser = serial.Serial(port, CSI_BAUD, timeout=0.1)
        ser.set_buffer_size(rx_size=32768)
        print(f"[CSI] Connected to {port}")

        while not stop_event.is_set():
            # 1. Read bulk data from serial
            data = ser.read(CSI_READ_SIZE)
            if not data:
                continue
                
            buffer.extend(data)
            
            # 2. Parse packets from buffer
            while True:
                # Find magic bytes
                idx = buffer.find(MAGIC_BYTE_SEQ)
                if idx == -1:
                    buffer = buffer[-2:]  # Keep last 2 bytes in case magic split
                    break
                
                if idx > 0:
                    buffer = buffer[idx:]  # Skip to magic
                
                # Check if we have enough for header
                if len(buffer) < 12:
                    break
                
                try:
                    # Parse ESP32 header: Magic(2) | Len(2) | RSSI(1) | Chan(1) | Time(4)
                    payload_len = struct.unpack('<H', buffer[2:4])[0]
                    total_packet_len = 12 + payload_len
                    
                    if len(buffer) < total_packet_len:
                        break  # Wait for more data
                    
                    # Extract metadata
                    rssi = buffer[4]
                    if rssi > 127:
                        rssi -= 256  # Convert to signed int8
                    
                    channel = buffer[5]
                    esp_time = struct.unpack('<I', buffer[6:10])[0]
                    payload = buffer[12:total_packet_len]
                    
                    # Create log entry with PC timestamp
                    # Format: PC_Time(8) + ESP_Time(4) + RSSI(1) + Channel(1) + Len(2) + Payload(N)
                    pc_time = time.time()
                    log_header = struct.pack('<dIbbH', pc_time, esp_time, rssi, channel, payload_len)
                    
                    # Add to chunk buffer for batched write
                    chunk_buffer.extend(log_header + payload)
                    packet_count += 1
                    
                    # Consume buffer
                    buffer = buffer[total_packet_len:]
                    
                    # Batch write when threshold reached
                    if len(chunk_buffer) >= CSI_WRITE_THRESHOLD:
                        _, clip_idx = csi_manager(bytes(chunk_buffer), len(chunk_buffer))
                        chunk_buffer.clear()
                    
                except Exception:
                    buffer = buffer[2:]  # Skip and retry
            
            # Send stats periodically
            if time.time() - last_stat_time > 0.5:
                # Flush any remaining chunk buffer
                if chunk_buffer:
                    _, clip_idx = csi_manager(bytes(chunk_buffer), len(chunk_buffer))
                    chunk_buffer.clear()
                
                duration = time.time() - last_stat_time
                pps = packet_count / duration if duration > 0 else 0
                
                stats_queue.put({
                    "type": "csi",
                    "clip": clip_idx,
                    "pps": pps,
                    "packets": packet_count,
                    "size_mb": os.path.getsize(f_out.name) / (1024*1024)
                })
                
                packet_count = 0
                last_stat_time = time.time()

    except Exception as e:
        stats_queue.put({"type": "error", "msg": f"CSI Crash: {e}"})
    finally:
        # Flush remaining data
        if chunk_buffer:
            csi_manager(bytes(chunk_buffer), len(chunk_buffer))
        
        if ser:
            ser.close()
        if f_out:
            f_out.close()

# ---------------------------------------------------------
# MAIN CONTROLLER
# ---------------------------------------------------------
def main():
    multiprocessing.freeze_support()
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    
    print("--- Initializing Hardware ---")
    
    # CSI Check
    found_csi_port = None
    ports = list_ports.comports()
    
    for port in ports:
        if 'USB Serial Device' in port.description or 'ESP32' in port.description:
            print(f"Found candidate port: {port.device}")
            found_csi_port = port.device
            break
    
    if found_csi_port is None:
        print(f"[WARNING] Cannot auto-find CSI device. Using default {CSI_PORT}.")
        found_csi_port = CSI_PORT
        
    # Audio Check
    try:
        p = pyaudio.PyAudio()
        print("--- Available Input Devices ---")
        device_ok = False
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                if info['maxInputChannels'] >= CHANNELS and info['defaultSampleRate'] == SAMPLE_RATE:
                    print(f"Index {i}: {info['name']} (Rate: {info['defaultSampleRate']} Hz)")
                    if i == AUDIO_DEVICE:
                        device_ok = True
        p.terminate()
        
        if not device_ok:
            print(f"[FATAL] Audio Device {AUDIO_DEVICE} not configured correctly for {SAMPLE_RATE}Hz.")
            return
            
    except Exception as e:
        print(f"[FATAL] PyAudio initialization failed: {e}")
        return

    # Setup workers
    stats_queue = multiprocessing.Queue()
    stop_event = multiprocessing.Event()
    
    sync_time_utc = time.time()
    meta = {
        "start_time_utc": sync_time_utc,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "audio_dtype": DTYPE_NPY,
        "csi_port": found_csi_port,
        "audio_device_index": AUDIO_DEVICE,
        "csi_format": "PC_Time(8) + ESP_Time(4) + RSSI(1) + Channel(1) + Len(2) + Payload(N)",
        "audio_format": "Magic(2) + Timestamp(8) + Length(4) + Audio_Data(N)"
    }
    with open(os.path.join(OUTPUT_DIR, "metadata.json"), 'w') as f:
        json.dump(meta, f, indent=4)

    # Launch workers
    p_audio = multiprocessing.Process(target=audio_worker, args=(stop_event, OUTPUT_DIR, stats_queue))
    p_csi = multiprocessing.Process(target=csi_worker, args=(stop_event, OUTPUT_DIR, stats_queue, found_csi_port))

    print(f"\n--- STARTING SESSION TO: {SESSION_NAME} ---")
    p_audio.start()
    p_csi.start()
    
    # Dashboard state
    state = {"pps": 0, "packets": 0, "csi_clip": 0, "csi_mb": 0, "aud_clip": 0, "aud_mb": 0, "duration": 0}
    start_time = time.time()

    try:
        while True:
            # Update state
            try:
                while True:
                    stat = stats_queue.get_nowait()
                    if stat['type'] == 'csi':
                        state['pps'] = stat['pps']
                        state['packets'] = stat.get('packets', 0)
                        state['csi_clip'] = stat['clip']
                        state['csi_mb'] = stat['size_mb']
                    elif stat['type'] == 'audio':
                        state['aud_clip'] = stat['clip']
                        state['aud_mb'] = stat['size_mb']
                    elif stat['type'] == 'error':
                        print(f"\n[ERROR] {stat['msg']}")
            except queue.Empty:
                pass

            # Update dashboard
            state['duration'] = time.time() - start_time
            
            pps_str = f"PPS: {state['pps']:04.0f}"
            pkt_str = f"Packets: {state['packets']:04d}"
            csi_clip_str = f"CSI: #{state['csi_clip']} ({state['csi_mb']:.0f} MB)"
            aud_clip_str = f"Audio: #{state['aud_clip']} ({state['aud_mb']:.0f} MB)"
            
            sys.stdout.write(f"\r[ {int(state['duration']):>5}s ] | {pps_str} | {pkt_str} | {csi_clip_str} | {aud_clip_str}    ")
            sys.stdout.flush()
            
            # Health check
            if not p_audio.is_alive() or not p_csi.is_alive():
                print("\n\n[FATAL] Process died. Stopping.")
                break
                
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n\n[SYSTEM] Stop signal received...")
    finally:
        stop_event.set()
        p_audio.join()
        p_csi.join()
        print("Session Saved. Dual-Stream Recording Complete.")

if __name__ == "__main__":
    main()