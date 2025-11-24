import struct
import csv
import sys
import os
import numpy as np

def convert_complex_to_csv(input_file):
    if not os.path.exists(input_file):
        print(f"Error: File {input_file} not found.")
        return

    output_file = input_file.replace(".raw", "_complex.csv")
    print(f"Converting {input_file} -> {output_file} ...")
    
    file_size = os.path.getsize(input_file)
    bytes_processed = 0
    packet_count = 0
    
    with open(input_file, 'rb') as f_in, open(output_file, 'w', newline='') as f_out:
        writer = None
        
        while True:
            # 1. Read Header
            header_data = f_in.read(15)
            if len(header_data) < 15: break
            bytes_processed += 15
            
            pc_time, esp_time, rssi, payload_len = struct.unpack('<dIbH', header_data)
            
            # 2. Read Payload
            payload = f_in.read(payload_len)
            if len(payload) < payload_len: break
            bytes_processed += payload_len

            try:
                # 3. RAW PARSING (No Math)
                # Convert bytes directly to signed integers (int8)
                # The data is stored as: [Real, Imag, Real, Imag, ...]
                raw_values = np.frombuffer(payload, dtype=np.int8)
                
                # Handle metadata bytes (remove trailing byte if odd length)
                if len(raw_values) % 2 != 0:
                    raw_values = raw_values[:-1]
                
                # 4. Setup CSV Header (First packet only)
                if writer is None:
                    num_subcarriers = len(raw_values) // 2
                    headers = ['pc_timestamp', 'esp_timestamp', 'rssi']
                    for i in range(num_subcarriers):
                        headers.append(f'sub{i}_real')
                        headers.append(f'sub{i}_imag')
                    
                    writer = csv.writer(f_out)
                    writer.writerow(headers)
                    print(f"Detected {num_subcarriers} subcarriers (Complex).")

                # 5. Write Row
                # We simply cast the numpy array to a list and append metadata
                # This writes [Time, Time, RSSI, R0, I0, R1, I1, R2, I2...]
                row = [f"{pc_time:.6f}", esp_time, rssi] + raw_values.tolist()
                writer.writerow(row)
                
                packet_count += 1
                
                if packet_count % 2000 == 0:
                    progress = (bytes_processed / file_size) * 100
                    sys.stdout.write(f"\rProgress: {progress:.1f}% | Packets: {packet_count}")
                    sys.stdout.flush()

            except Exception:
                continue

    print(f"\n\nSuccess! Saved to {output_file}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python csi_complex_converter.py <filename.raw>")
    else:
        convert_complex_to_csv(sys.argv[1])