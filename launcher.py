import os
import sys
import threading
import time
import socket
import webbrowser
from pathlib import Path

# Add current dir to path so we can import server
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import server

def find_free_port(start: int = 3456) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start

def start_server(port):
    server.DEFAULT_PORT = port
    # Run the server logic
    # Note: we use the same Handler and ThreadingHTTPServer from server.py
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
    httpd.serve_forever()

def main():
    # Set up macOS Application Support directory
    app_name = "Daily Task Manager"
    app_support = Path.home() / "Library" / "Application Support" / app_name
    app_support.mkdir(parents=True, exist_ok=True)
    
    # Export env var for server.py to pick up
    os.environ["APP_DATA_DIR"] = str(app_support)
    
    # Find a port and start server in background
    port = find_free_port()
    server_thread = threading.Thread(target=start_server, args=(port,), daemon=True)
    server_thread.start()
    
    url = f"http://127.0.0.1:{port}"
    print(f"Server started at {url}")
    
    # Wait a moment for server to be ready
    time.sleep(0.5)
    
    try:
        import webview
        # Create a native window
        window = webview.create_window(
            app_name, 
            url, 
            width=1200, 
            height=800,
            min_size=(800, 600)
        )
        webview.start()
    except ImportError:
        # Fallback to default browser if pywebview is not available
        print("pywebview not found, falling back to default browser.")
        webbrowser.open(url)
        # Keep the script alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

if __name__ == "__main__":
    main()
