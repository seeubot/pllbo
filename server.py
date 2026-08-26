import http.server
import json
import os
import time
import hmac
import hashlib
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs

PORT = int(os.getenv('PORT', '8000'))
API_SECRET = os.getenv('API_SECRET', 'mayatv')

def load_channels():
    try:
        with open('channels.json', 'r') as f:
            return json.load(f)
    except:
        return []

CHANNELS = load_channels()

class SecureHandler(http.server.SimpleHTTPRequestHandler):
    
    def log_message(self, format, *args):
        pass
    
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        
        if path == '/':
            self.serve_index()
        elif path == '/player.html':
            self.serve_player_page(parsed)
        elif path == '/stream':
            self.serve_stream_proxy(parsed)
        else:
            self.send_error(404)
    
    def serve_index(self):
        """Serve main page - no stream URLs"""
        html = self.render_index()
        self.send_html(html)
    
    def serve_player_page(self, parsed):
        """Serve player page - NO stream URL in source"""
        params = parse_qs(parsed.query)
        channel_id = params.get('id', [''])[0]
        
        if not channel_id:
            self.send_html("<h1>No channel</h1>", 400)
            return
        
        channel = next((ch for ch in CHANNELS if ch['id'] == channel_id), None)
        
        if not channel:
            self.send_html("<h1>Channel not found</h1>", 404)
            return
        
        # Generate token
        expires = int(time.time()) + 300
        token = self.generate_token(channel_id, expires)
        
        # The stream URL is NEVER sent to client
        # Client only gets a proxy URL
        proxy_url = f"/stream?id={channel_id}&token={token}&expires={expires}"
        
        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{channel['name']} - Chill Box</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.12/dist/hls.min.js"></script>
    <style>
        body {{ background: #000; margin: 0; height: 100vh; overflow: hidden; font-family: sans-serif; }}
        .player-wrapper {{ width: 100%; height: 100%; position: relative; }}
        video {{ width: 100%; height: 100%; object-fit: contain; }}
        .watermark {{ position: absolute; top: 20px; right: 20px; color: rgba(255,255,255,0.4); font-size: 11px; z-index: 10; pointer-events: none; }}
        .back-btn {{ position: absolute; top: 20px; left: 20px; background: rgba(0,0,0,0.5); color: #fff; border: none; padding: 10px 16px; border-radius: 8px; cursor: pointer; z-index: 20; }}
        .loading {{ position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); text-align: center; z-index: 30; color: #fff; }}
        .spinner {{ width: 40px; height: 40px; border: 3px solid rgba(255,255,255,0.2); border-top: 3px solid #E50914; border-radius: 50%; animation: spin 1s linear infinite; margin: 0 auto 10px; }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
    </style>
</head>
<body>
    <div class="player-wrapper">
        <button class="back-btn" onclick="window.location.href='/'">Back</button>
        <div class="watermark">CHILL BOX</div>
        <video id="video" controls autoplay playsinline></video>
        <div class="loading" id="loading">
            <div class="spinner"></div>
            <span>Loading...</span>
        </div>
    </div>

    <script>
        // Only proxy URL is visible - real stream URL is hidden
        const PROXY_URL = {json.dumps(proxy_url)};
        
        let hls = null;
        
        function playStream() {{
            const video = document.getElementById('video');
            const loading = document.getElementById('loading');
            
            if (Hls.isSupported()) {{
                hls = new Hls({{
                    maxBufferLength: 30,
                    xhrSetup: (xhr) => {{
                        xhr.setRequestHeader('Referer', window.location.origin);
                    }}
                }});
                
                hls.loadSource(PROXY_URL);
                hls.attachMedia(video);
                
                hls.on(Hls.Events.MANIFEST_PARSED, () => {{
                    loading.style.display = 'none';
                    video.play();
                }});
                
                hls.on(Hls.Events.ERROR, (event, data) => {{
                    if (data.fatal) {{
                        loading.innerHTML = '<span>Stream error</span>';
                    }}
                }});
            }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                video.src = PROXY_URL;
                video.addEventListener('loadedmetadata', () => {{
                    loading.style.display = 'none';
                    video.play();
                }});
            }}
        }}
        
        playStream();
    </script>
</body>
</html>'''
        
        self.send_html(html)
    
    def serve_stream_proxy(self, parsed):
        """Proxy the actual stream - real URL never exposed"""
        params = parse_qs(parsed.query)
        channel_id = params.get('id', [''])[0]
        token = params.get('token', [''])[0]
        expires = params.get('expires', [''])[0]
        
        # Validate token
        if not self.validate_token(channel_id, token, expires):
            self.send_error(403)
            return
        
        # Check expiry
        if int(expires) < int(time.time()):
            self.send_error(403)
            return
        
        # Get channel
        channel = next((ch for ch in CHANNELS if ch['id'] == channel_id), None)
        
        if not channel:
            self.send_error(404)
            return
        
        real_url = channel['url']
        
        # Fetch the real stream
        try:
            req = urllib.request.Request(real_url, headers={
                'User-Agent': 'Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36',
                'Referer': 'https://www.jiotv.com/',
            })
            
            response = urllib.request.urlopen(req, timeout=30)
            
            # Forward headers
            self.send_response(200)
            self.send_header('Content-Type', response.headers.get('Content-Type', 'application/vnd.apple.mpegurl'))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-store')
            
            # Check if it's a playlist (m3u8) or segment (.ts)
            if '.m3u8' in real_url:
                # Read playlist content
                content = response.read().decode('utf-8', errors='ignore')
                
                # Rewrite URLs in the playlist to go through proxy
                content = self.rewrite_playlist(content, channel_id, token, expires)
                
                self.end_headers()
                self.wfile.write(content.encode())
            else:
                # Stream binary content (segments)
                self.end_headers()
                
                # Stream in chunks
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        
        except urllib.error.HTTPError as e:
            self.send_error(e.code)
        except Exception as e:
            self.send_error(500)
    
    def rewrite_playlist(self, playlist_content, channel_id, token, expires):
        """Rewrite playlist URLs to go through proxy"""
        lines = playlist_content.split('\n')
        rewritten = []
        
        for line in lines:
            line = line.strip()
            
            if line.startswith('#'):
                # It's a directive - keep as is
                rewritten.append(line)
            elif line.startswith('http'):
                # It's a URL - rewrite to proxy
                proxy_url = f"/stream?id={channel_id}&token={token}&expires={expires}&segment={line}"
                rewritten.append(proxy_url)
            elif line:
                # Relative URL - keep as is
                rewritten.append(line)
            else:
                rewritten.append('')
        
        return '\n'.join(rewritten)
    
    def generate_token(self, channel_id, expires):
        """Generate HMAC token"""
        token_data = f"{channel_id}:{expires}"
        token = hmac.new(
            API_SECRET.encode(),
            token_data.encode(),
            hashlib.sha256
        ).hexdigest()
        return token
    
    def validate_token(self, channel_id, token, expires):
        """Validate HMAC token"""
        expected = self.generate_token(channel_id, expires)
        return hmac.compare_digest(token, expected)
    
    def render_index(self):
        """Render main page"""
        channel_cards = ""
        categories = {}
        
        for ch in CHANNELS:
            cat = ch.get('category', 'General')
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(ch)
        
        for category, channels in categories.items():
            channel_cards += f'<div class="category-section">'
            channel_cards += f'<div class="category-title">{category}</div>'
            channel_cards += f'<div class="channel-grid">'
            
            for ch in channels:
                initial = ch['name'][0].upper() if ch['name'] else '?'
                logo_html = f'<img src="{ch["logo"]}">' if ch.get('logo') else initial
                
                channel_cards += f'''
                    <div class="channel-card" onclick="window.location.href='/player.html?id={ch["id"]}'">
                        <div class="channel-logo">{logo_html}</div>
                        <div class="channel-name">{ch['name']}</div>
                    </div>
                '''
            
            channel_cards += '</div></div>'
        
        return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chill Box</title>
    <style>
        body {{ background: #0a0a0f; color: #fff; font-family: sans-serif; margin: 0; padding: 20px; }}
        .header {{ text-align: center; margin-bottom: 30px; }}
        .logo {{ font-size: 24px; font-weight: 900; }}
        .logo span {{ color: #E50914; }}
        .category-section {{ margin-bottom: 24px; }}
        .category-title {{ font-size: 14px; color: #9EA8B6; margin-bottom: 10px; text-transform: uppercase; }}
        .channel-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; }}
        .channel-card {{ background: #141722; border-radius: 10px; padding: 15px; text-align: center; cursor: pointer; }}
        .channel-card:hover {{ border: 1px solid #E50914; }}
        .channel-logo {{ width: 50px; height: 50px; margin: 0 auto 10px; border-radius: 50%; background: #1a1a2e; display: flex; align-items: center; justify-content: center; font-size: 20px; font-weight: bold; color: #E50914; overflow: hidden; }}
        .channel-logo img {{ width: 100%; height: 100%; object-fit: contain; }}
        .channel-name {{ font-size: 13px; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">Chill<span>Box</span></div>
    </div>
    {channel_cards}
</body>
</html>'''
    
    def send_html(self, html, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(html.encode())

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"Chill Box Server on port {PORT}")
    
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), SecureHandler) as httpd:
        httpd.serve_forever()
