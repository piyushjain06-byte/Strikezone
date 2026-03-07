#!/usr/bin/env python
"""
Run the StrikeZone server with WebSocket support.
Usage: python run.py
"""
import os
import sys
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'strikezone.settings')

def main():
    port = sys.argv[1] if len(sys.argv) > 1 else '8000'
    print(f"\n🏏 Starting StrikeZone with WebSocket support on port {port}...")
    print(f"   Open: http://127.0.0.1:{port}\n")
    
    # Try daphne first
    try:
        subprocess.run([
            sys.executable, '-m', 'daphne',
            '-p', port,
            'strikezone.asgi:application'
        ])
    except (FileNotFoundError, ModuleNotFoundError):
        # Fall back to uvicorn
        try:
            subprocess.run([
                sys.executable, '-m', 'uvicorn',
                f'--port={port}',
                '--reload',
                'strikezone.asgi:application'
            ])
        except (FileNotFoundError, ModuleNotFoundError):
            print("❌ Neither daphne nor uvicorn found.")
            print("   Run: pip install daphne")
            print("   Then: python run.py")

if __name__ == '__main__':
    main()
