import os
import sys
import json
import struct
import numpy as np
import h5py
from scipy.io import wavfile
from pathlib import Path
from tqdm import tqdm

# --- CONFIGURATION ---
AUDIO_HEADER_STRUCT = struct.Struct('<2sdI')
MAGIC_AUDIO = b'\xAA\xAA'

CSI_PACKET_HEADER = struct.Struct('<dIbbH')
# Format: PC_Time(8) + ESP_Time(4) + RSSI(1) + Channel(1) + Len(2)

def parse_audio_binary(file_path, sample_rate=44100):
    """
    Parse audio binary file into numpy array.
    Format: [Magic(2) + Timestamp(8) + Length(4) + Audio_Data(N)] repeated
    """
    audio_chunks = []
    timestamps = []
    
    with open(file_path, 'rb') as f:
        file_size = os.path.getsize(file_path)
        pbar = tqdm(total=file_size, desc=f"Parsing {os.path.basename(file_path)}", unit='B', unit_scale=True)
        
        while True:
            # Read header
            header_bytes = f.read(14)  # 2 + 8 + 4 = 14 bytes
            if len(header_bytes) < 14:
                break
            
            magic, timestamp, length = AUDIO_HEADER_STRUCT.unpack(header_bytes)
            
            if magic != MAGIC_AUDIO:
                print(f"\n[WARNING] Invalid magic bytes at position {f.tell()-14}, skipping...")
                f.seek(-13, 1)  # Backtrack and try next byte
                continue
            
            # Read audio data
            data_bytes = f.read(length)
            if len(data_bytes) < length:
                break
            
            # Convert to int16 array
            audio_samples = np.frombuffer(data_bytes, dtype='int16')
            audio_chunks.append(audio_samples)
            timestamps.append(timestamp)
            
            pbar.update(14 + length)
        
        pbar.close()
    
    # Concatenate all chunks
    full_audio = np.concatenate(audio_chunks) if audio_chunks else np.array([], dtype='int16')
    
    return full_audio, np.array(timestamps)

def parse_csi_binary(file_path):
    """
    Parse CSI binary file into structured data.
    Format: [PC_Time(8) + ESP_Time(4) + RSSI(1) + Channel(1) + Len(2) + Payload(N)] repeated
    """
    packets = {
        'pc_timestamps': [],
        'esp_timestamps': [],
        'rssi': [],
        'channel': [],
        'csi_data': []
    }
    
    with open(file_path, 'rb') as f:
        file_size = os.path.getsize(file_path)
        pbar = tqdm(total=file_size, desc=f"Parsing {os.path.basename(file_path)}", unit='B', unit_scale=True)
        
        while True:
            # Read packet header
            header_bytes = f.read(16)  # 8 + 4 + 1 + 1 + 2 = 16 bytes
            if len(header_bytes) < 16:
                break
            
            try:
                pc_time, esp_time, rssi, channel, payload_len = CSI_PACKET_HEADER.unpack(header_bytes)
                
                # Read payload
                payload = f.read(payload_len)
                if len(payload) < payload_len:
                    break
                
                # Store packet data
                packets['pc_timestamps'].append(pc_time)
                packets['esp_timestamps'].append(esp_time)
                packets['rssi'].append(rssi)
                packets['channel'].append(channel)
                packets['csi_data'].append(np.frombuffer(payload, dtype='uint8'))
                
                pbar.update(16 + payload_len)
                
            except struct.error:
                break
        
        pbar.close()
    
    # Convert lists to arrays
    for key in ['pc_timestamps', 'esp_timestamps', 'rssi', 'channel']:
        packets[key] = np.array(packets[key])
    
    return packets

def convert_session(session_dir):
    """
    Convert a full capture session to WAV and HDF5 formats.
    """
    session_path = Path(session_dir)
    
    if not session_path.exists():
        print(f"[ERROR] Session directory not found: {session_dir}")
        return
    
    # Load metadata
    metadata_path = session_path / "metadata.json"
    if not metadata_path.exists():
        print(f"[ERROR] metadata.json not found in {session_dir}")
        return
    
    with open(metadata_path, 'r') as f:
        metadata = json.load(f)
    
    print(f"\n{'='*60}")
    print(f"Converting Session: {session_path.name}")
    print(f"{'='*60}")
    print(f"Start Time: {metadata['start_time_utc']}")
    print(f"Sample Rate: {metadata['sample_rate']} Hz")
    print(f"CSI Port: {metadata['csi_port']}")
    print(f"{'='*60}\n")
    
    # Find all audio and CSI files
    audio_files = sorted(session_path.glob("audio_part_*.bin"))
    csi_files = sorted(session_path.glob("csi_part_*.bin"))
    
    print(f"Found {len(audio_files)} audio files")
    print(f"Found {len(csi_files)} CSI files\n")
    
    # --- CONVERT AUDIO ---
    if audio_files:
        print("🎵 Processing Audio Files...")
        all_audio = []
        all_audio_timestamps = []
        
        for audio_file in audio_files:
            audio_data, timestamps = parse_audio_binary(audio_file, metadata['sample_rate'])
            all_audio.append(audio_data)
            all_audio_timestamps.append(timestamps)
        
        # Concatenate all audio
        full_audio = np.concatenate(all_audio)
        full_audio_timestamps = np.concatenate(all_audio_timestamps)
        
        # Save as WAV
        wav_path = session_path / "audio_complete.wav"
        wavfile.write(wav_path, metadata['sample_rate'], full_audio)
        print(f"✅ Saved WAV: {wav_path}")
        print(f"   Duration: {len(full_audio) / metadata['sample_rate']:.2f} seconds")
        print(f"   Samples: {len(full_audio):,}")
        print(f"   Chunks: {len(full_audio_timestamps)}\n")
        
        # Save audio timestamps
        np.save(session_path / "audio_timestamps.npy", full_audio_timestamps)
    
    # --- CONVERT CSI ---
    if csi_files:
        print("📡 Processing CSI Files...")
        all_csi_data = {
            'pc_timestamps': [],
            'esp_timestamps': [],
            'rssi': [],
            'channel': [],
            'csi_data': []
        }
        
        for csi_file in csi_files:
            packets = parse_csi_binary(csi_file)
            for key in all_csi_data.keys():
                if key == 'csi_data':
                    all_csi_data[key].extend(packets[key])
                else:
                    all_csi_data[key].append(packets[key])
        
        # Concatenate arrays
        for key in ['pc_timestamps', 'esp_timestamps', 'rssi', 'channel']:
            all_csi_data[key] = np.concatenate(all_csi_data[key]) if all_csi_data[key] else np.array([])
        
        print(f"✅ Parsed {len(all_csi_data['pc_timestamps'])} CSI packets")
        
        if len(all_csi_data['pc_timestamps']) > 0:
            duration = all_csi_data['pc_timestamps'][-1] - all_csi_data['pc_timestamps'][0]
            avg_pps = len(all_csi_data['pc_timestamps']) / duration if duration > 0 else 0
            print(f"   Duration: {duration:.2f} seconds")
            print(f"   Average PPS: {avg_pps:.1f}")
            print(f"   RSSI Range: [{all_csi_data['rssi'].min()}, {all_csi_data['rssi'].max()}] dBm\n")
    
    # --- CREATE HDF5 ---
    print("💾 Creating HDF5 Archive...")
    h5_path = session_path / "session_data.h5"
    
    with h5py.File(h5_path, 'w') as h5f:
        # Store metadata
        meta_group = h5f.create_group('metadata')
        for key, value in metadata.items():
            meta_group.attrs[key] = value
        
        # Store audio
        if audio_files:
            audio_group = h5f.create_group('audio')
            audio_group.create_dataset('samples', data=full_audio, compression='gzip', compression_opts=4)
            audio_group.create_dataset('timestamps', data=full_audio_timestamps, compression='gzip', compression_opts=4)
            audio_group.attrs['sample_rate'] = metadata['sample_rate']
            audio_group.attrs['channels'] = metadata['channels']
            audio_group.attrs['duration_seconds'] = len(full_audio) / metadata['sample_rate']
        
        # Store CSI
        if csi_files and len(all_csi_data['pc_timestamps']) > 0:
            csi_group = h5f.create_group('csi')
            csi_group.create_dataset('pc_timestamps', data=all_csi_data['pc_timestamps'], compression='gzip', compression_opts=4)
            csi_group.create_dataset('esp_timestamps', data=all_csi_data['esp_timestamps'], compression='gzip', compression_opts=4)
            csi_group.create_dataset('rssi', data=all_csi_data['rssi'], compression='gzip', compression_opts=4)
            csi_group.create_dataset('channel', data=all_csi_data['channel'], compression='gzip', compression_opts=4)
            
            # Store CSI data as variable-length dataset
            dt = h5py.vlen_dtype(np.dtype('uint8'))
            csi_data_dset = csi_group.create_dataset('csi_data', (len(all_csi_data['csi_data']),), dtype=dt)
            for i, csi_packet in enumerate(all_csi_data['csi_data']):
                csi_data_dset[i] = csi_packet
            
            csi_group.attrs['packet_count'] = len(all_csi_data['pc_timestamps'])
    
    print(f"✅ Saved HDF5: {h5_path}")
    print(f"   Size: {os.path.getsize(h5_path) / (1024**2):.2f} MB\n")
    
    # --- SUMMARY ---
    print(f"{'='*60}")
    print("✨ Conversion Complete!")
    print(f"{'='*60}")
    print(f"Output files in: {session_path}")
    if audio_files:
        print(f"  📄 audio_complete.wav")
        print(f"  📄 audio_timestamps.npy")
    print(f"  📄 session_data.h5")
    print(f"{'='*60}\n")

def load_from_hdf5(h5_path):
    """
    Example function to load data from HDF5.
    """
    print(f"\n📖 Reading HDF5: {h5_path}\n")
    
    with h5py.File(h5_path, 'r') as h5f:
        print("Available groups:")
        for key in h5f.keys():
            print(f"  - {key}")
        
        print("\nMetadata:")
        for key, value in h5f['metadata'].attrs.items():
            print(f"  {key}: {value}")
        
        if 'audio' in h5f:
            print("\nAudio:")
            print(f"  Shape: {h5f['audio/samples'].shape}")
            print(f"  Duration: {h5f['audio'].attrs['duration_seconds']:.2f}s")
        
        if 'csi' in h5f:
            print("\nCSI:")
            print(f"  Packets: {h5f['csi'].attrs['packet_count']}")
            print(f"  RSSI range: [{h5f['csi/rssi'][:].min()}, {h5f['csi/rssi'][:].max()}]")

def main():
    if len(sys.argv) < 2:
        print("Usage: python converter.py <session_directory>")
        print("\nExample:")
        print("  python converter.py multimodal_capture_1763961122")
        print("\nThis will create:")
        print("  - audio_complete.wav")
        print("  - audio_timestamps.npy")
        print("  - session_data.h5")
        return
    
    session_dir = sys.argv[1]
    convert_session(session_dir)
    
    # Optionally load and display HDF5 info
    h5_path = Path(session_dir) / "session_data.h5"
    if h5_path.exists():
        load_from_hdf5(h5_path)

if __name__ == "__main__":
    main()