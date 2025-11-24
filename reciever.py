import serial
import threading
import time
import struct
import queue
import signal
import sys
import os

# --- CONFIGURATION ---
SERIAL_PORT = "COM5" 
BAUD_RATE = 2000000
MAGIC_BYTE_SEQ = b'\xfa\xfa'
LOG_FILENAME = f"csi_capture_{int(time.time())}.raw"

# Queues
raw_queue = queue.Queue()      # USB -> Parser
write_queue = queue.Queue()    # Parser -> Disk

running = True
packet_count = 0
bytes_written = 0

class IOThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.ser = None

    def run(self):
        global running
        try:
            self.ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
            self.ser.set_buffer_size(rx_size=32768) 
            print(f"[IO] Connected to {SERIAL_PORT}")
            
            while running:
                data = self.ser.read(4096 * 8) # Bulk read
                if data:
                    raw_queue.put(data)
        except Exception as e:
            print(f"[IO] Error: {e}")
            running = False
    
    def stop(self):
        if self.ser: self.ser.close()

class ParserThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.buffer = bytearray()

    def run(self):
        global running, packet_count
        print("[Parser] Engine Active")
        
        while running:
            if not raw_queue.empty():
                self.buffer.extend(raw_queue.get())
                
                while True:
                    # 1. Find Magic
                    idx = self.buffer.find(MAGIC_BYTE_SEQ)
                    if idx == -1:
                        self.buffer = self.buffer[-2:]
                        break
                    
                    if idx > 0:
                        self.buffer = self.buffer[idx:]

                    # 2. Check Header Size (Magic + Len + RSSI + Chan + Time)
                    if len(self.buffer) < 12:
                        break

                    # 3. Parse Length
                    try:
                        # Header Format from ESP32:
                        # Magic(2) | Len(2) | RSSI(1) | Chan(1) | Time(4)
                        payload_len = struct.unpack('<H', self.buffer[2:4])[0]
                        total_packet_len = 12 + payload_len
                        
                        if len(self.buffer) < total_packet_len:
                            break

                        # 4. Extract Metadata
                        rssi = self.buffer[4]
                        if rssi > 127: rssi -= 256 # Signed int8 fix
                        
                        esp_time = struct.unpack('<I', self.buffer[6:10])[0]
                        payload = self.buffer[12:total_packet_len]
                        
                        # 5. Create Log Entry
                        # PC_Time(8) + ESP_Time(4) + RSSI(1) + Len(2) + Payload(N)
                        pc_time = time.time()
                        
                        # Struct format: d (double), I (uint32), b (int8), H (uint16)
                        header = struct.pack('<dIbH', pc_time, esp_time, rssi, payload_len)
                        
                        write_queue.put(header + payload)
                        packet_count += 1
                        
                        # Consume buffer
                        self.buffer = self.buffer[total_packet_len:]

                    except Exception:
                        self.buffer = self.buffer[2:] # Skip and retry

            else:
                time.sleep(0.001)

class DiskWriterThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.chunk_buffer = bytearray()
        self.write_threshold = 1024 * 128 # Write to disk every 128KB

    def run(self):
        global running, bytes_written
        print(f"[Disk] Logging to {LOG_FILENAME}")
        
        with open(LOG_FILENAME, 'wb') as f:
            while running or not write_queue.empty():
                try:
                    # Get data
                    data = write_queue.get(timeout=0.1)
                    self.chunk_buffer.extend(data)
                    
                    # Batch write to save SSD IOPS
                    if len(self.chunk_buffer) >= self.write_threshold:
                        f.write(self.chunk_buffer)
                        bytes_written += len(self.chunk_buffer)
                        self.chunk_buffer.clear()
                        
                except queue.Empty:
                    continue
            
            # Flush remaining
            if self.chunk_buffer:
                f.write(self.chunk_buffer)
                bytes_written += len(self.chunk_buffer)

if __name__ == "__main__":
    io = IOThread()
    parser = ParserThread()
    writer = DiskWriterThread()
    
    io.start()
    parser.start()
    writer.start()
    
    start_time = time.time()
    
    try:
        while True:
            time.sleep(1)
            elapsed = time.time() - start_time
            size_mb = bytes_written / (1024 * 1024)
            rate = packet_count / elapsed
            
            sys.stdout.write(f"\r[REC] Packets: {packet_count} | Size: {size_mb:.2f} MB | Rate: {rate:.0f} PPS")
            sys.stdout.flush()
            
    except KeyboardInterrupt:
        print("\nStopping...")
        running = False
        io.join()
        parser.join()
        writer.join()
        print(f"\nSaved {bytes_written} bytes to {LOG_FILENAME}")