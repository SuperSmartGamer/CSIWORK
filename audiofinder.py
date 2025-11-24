import pyaudio
import numpy as np
import time

def list_audio_devices():
    """
    List all available audio input devices with detailed info.
    """
    p = pyaudio.PyAudio()
    
    print("\n" + "="*80)
    print("AVAILABLE AUDIO INPUT DEVICES")
    print("="*80)
    
    valid_devices = []
    
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        
        # Only show input devices
        if info['maxInputChannels'] > 0:
            valid_devices.append((i, info))
            
            print(f"\n[Device {i}]")
            print(f"  Name: {info['name']}")
            print(f"  Channels: {info['maxInputChannels']}")
            print(f"  Sample Rate: {info['defaultSampleRate']} Hz")
            print(f"  Host API: {p.get_host_api_info_by_index(info['hostApi'])['name']}")
            
            # Highlight if it's the default
            if i == p.get_default_input_device_info()['index']:
                print(f"  ⭐ DEFAULT INPUT DEVICE")
    
    p.terminate()
    
    print("\n" + "="*80)
    print(f"Found {len(valid_devices)} input devices")
    print("="*80 + "\n")
    
    return valid_devices

def test_device(device_index, duration=3, sample_rate=44100):
    """
    Test record from a specific device to verify it works.
    """
    p = pyaudio.PyAudio()
    
    try:
        print(f"\n🎤 Testing Device {device_index}...")
        print(f"Recording for {duration} seconds...")
        
        stream = p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=sample_rate,
            input=True,
            frames_per_buffer=1024,
            input_device_index=device_index
        )
        
        frames = []
        start_time = time.time()
        
        while time.time() - start_time < duration:
            data = stream.read(1024, exception_on_overflow=False)
            frames.append(np.frombuffer(data, dtype=np.int16))
        
        stream.stop_stream()
        stream.close()
        
        # Analyze recording
        audio_data = np.concatenate(frames)
        max_amplitude = np.abs(audio_data).max()
        rms = np.sqrt(np.mean(audio_data.astype(float)**2))
        
        print(f"✅ Recording successful!")
        print(f"   Samples: {len(audio_data):,}")
        print(f"   Max Amplitude: {max_amplitude} / 32768")
        print(f"   RMS Level: {rms:.1f}")
        
        if max_amplitude < 100:
            print(f"   ⚠️  WARNING: Very quiet signal - check if correct device!")
        elif max_amplitude > 30000:
            print(f"   ⚠️  WARNING: Signal might be clipping!")
        else:
            print(f"   ✅ Signal level looks good!")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing device: {e}")
        return False
    finally:
        p.terminate()

def find_device_by_name(search_term):
    """
    Find device index by partial name match.
    """
    p = pyaudio.PyAudio()
    
    matches = []
    
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0:
            if search_term.lower() in info['name'].lower():
                matches.append((i, info['name']))
    
    p.terminate()
    
    if matches:
        print(f"\n🔍 Found {len(matches)} device(s) matching '{search_term}':")
        for idx, name in matches:
            print(f"   [{idx}] {name}")
        return [m[0] for m in matches]
    else:
        print(f"❌ No devices found matching '{search_term}'")
        return []

def interactive_selector():
    """
    Interactive device selection with testing.
    """
    devices = list_audio_devices()
    
    if not devices:
        print("No input devices found!")
        return None
    
    while True:
        print("\nOptions:")
        print("  [number] - Test a specific device")
        print("  [s] - Search by name")
        print("  [q] - Quit")
        
        choice = input("\nYour choice: ").strip().lower()
        
        if choice == 'q':
            return None
        
        if choice == 's':
            search = input("Enter device name to search: ").strip()
            matches = find_device_by_name(search)
            if len(matches) == 1:
                if input(f"\nTest device {matches[0]}? (y/n): ").lower() == 'y':
                    if test_device(matches[0]):
                        if input(f"\nUse device {matches[0]}? (y/n): ").lower() == 'y':
                            return matches[0]
            continue
        
        try:
            device_idx = int(choice)
            if any(d[0] == device_idx for d in devices):
                if test_device(device_idx):
                    if input(f"\nUse device {device_idx}? (y/n): ").lower() == 'y':
                        return device_idx
            else:
                print(f"❌ Invalid device index: {device_idx}")
        except ValueError:
            print("❌ Invalid input")

def main():
    print("\n" + "="*80)
    print("PyAudio Device Selector & Tester")
    print("="*80)
    
    # Mode selection
    print("\nModes:")
    print("  [1] List all devices")
    print("  [2] Test specific device")
    print("  [3] Interactive selector")
    print("  [4] Search by name")
    
    mode = input("\nSelect mode (1-4): ").strip()
    
    if mode == '1':
        list_audio_devices()
    
    elif mode == '2':
        device_idx = int(input("Enter device index to test: "))
        test_device(device_idx)
    
    elif mode == '3':
        selected = interactive_selector()
        if selected is not None:
            print(f"\n✅ Selected device: {selected}")
            print(f"\nTo use in your script, set:")
            print(f"AUDIO_DEVICE = {selected}")
    
    elif mode == '4':
        search = input("Enter search term: ").strip()
        matches = find_device_by_name(search)
        if matches:
            print(f"\nTo use in your script, set:")
            print(f"AUDIO_DEVICE = {matches[0]}")
    
    else:
        print("Invalid mode")

if __name__ == "__main__":
    main()