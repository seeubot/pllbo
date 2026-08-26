import http.server
import json
import os
import time
import hmac
import hashlib
import base64
from urllib.parse import urlparse, parse_qs

PORT = int(os.getenv('PORT', '8000'))
API_SECRET = os.getenv('API_SECRET', 'mayatv')

# Load channels
def load_channels():
    try:
        with open('channels.json', 'r') as f:
            return json.load(f)
    except:
        return []

CHANNELS = load_channels()

class SecureHandler(http.server.SimpleHTTPRequestHandler):
    
    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/api/get-channels':
            self.handle_get_channels()
        elif parsed.path == '/api/get-stream':
            self.handle_get_stream(parsed)
        elif parsed.path == '/':
            self.serve_file('index.html')
        elif parsed.path == '/player.html':
            self.serve_file('player.html')
        else:
            super().do_GET()
    
    def handle_get_channels(self):
        """Return channel list without stream URLs"""
        # Validate API key
        api_key = self.headers.get('X-API-Key', '')
        if api_key != API_SECRET:
            self.send_json({'success': False, 'error': 'Unauthorized'}, 403)
            return
        
        # Only return channel names and IDs (no stream URLs)
        safe_channels = []
        for ch in CHANNELS:
            safe_channels.append({
                'id': ch['id'],
                'name': ch['name'],
                'category': ch.get('category', 'General'),
                'logo': ch.get('logo', '')
            })
        
        self.send_json({'success': True, 'channels': safe_channels})
    
    def handle_get_stream(self, parsed):
        """Return stream URL for a specific channel"""
        # Validate API key
        api_key = self.headers.get('X-API-Key', '')
        if api_key != API_SECRET:
            self.send_json({'success': False, 'error': 'Unauthorized'}, 403)
            return
        
        # Get channel ID
        params = parse_qs(parsed.query)
        channel_id = params.get('id', [''])[0]
        
        if not channel_id:
            self.send_json({'success': False, 'error': 'No channel ID'}, 400)
            return
        
        # Find channel
        channel = next((ch for ch in CHANNELS if ch['id'] == channel_id), None)
        
        if not channel:
            self.send_json({'success': False, 'error': 'Channel not found'}, 404)
            return
        
        # Return stream URL with short expiry
        expires = int(time.time()) + 120  # 2 minutes
        
        # Create token
        token_data = f"{channel_id}:{expires}:{api_key}"
        token = hmac.new(
            API_SECRET.encode(),
            token_data.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Build response
        response = {
            'success': True,
            'streamUrl': channel['url'],
            'drm': channel.get('drm', {}),
            'expires': expires,
            'token': token
        }
        
        self.send_json(response)
    
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def serve_file(self, filename):
        try:
            with open(filename, 'rb') as f:
                content = f.read()
            
            self.send_response(200)
            if filename.endswith('.html'):
                self.send_header('Content-Type', 'text/html')
            elif filename.endswith('.js'):
                self.send_header('Content-Type', 'application/javascript')
            elif filename.endswith('.css'):
                self.send_header('Content-Type', 'text/css')
            
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404)

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), SecureHandler) as httpd:
        print(f"Server running on port {PORT}")
        httpd.serve_forever()
