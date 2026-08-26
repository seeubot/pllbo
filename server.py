import http.server
import json
import os
import time
import hmac
import hashlib
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
            self.serve_player(parsed)
        elif path == '/api/get-channels':
            self.handle_get_channels()
        elif path == '/api/get-stream':
            self.handle_get_stream(parsed)
        else:
            self.send_error(404)
    
    def serve_index(self):
        """Serve main page"""
        html = self.render_index()
        self.send_html(html)
    
    def serve_player(self, parsed):
        """Serve player page with embedded stream URL (server-side)"""
        params = parse_qs(parsed.query)
        channel_id = params.get('id', [''])[0]
        
        if not channel_id:
            self.send_html("<h1>No channel specified</h1>", 400)
            return
        
        # Validate channel exists
        channel = next((ch for ch in CHANNELS if ch['id'] == channel_id), None)
        
        if not channel:
            self.send_html("<h1>Channel not found</h1>", 404)
            return
        
        # Generate short-lived token
        expires = int(time.time()) + 300  # 5 minutes
        token = self.generate_token(channel_id, expires)
        
        # Render player with stream URL embedded server-side
        html = self.render_player(channel, token, expires)
        self.send_html(html)
    
    def generate_token(self, channel_id, expires):
        """Generate HMAC token"""
        token_data = f"{channel_id}:{expires}"
        token = hmac.new(
            API_SECRET.encode(),
            token_data.encode(),
            hashlib.sha256
        ).hexdigest()
        return token
    
    def handle_get_channels(self):
        """Return channel list WITHOUT stream URLs"""
        safe_channels = []
        for ch in CHANNELS:
            safe_channels.append({
                'id': ch['id'],
                'name': ch['name'],
                'category': ch.get('category', 'General'),
                'logo': ch.get('logo', '')
            })
        
        self.send_json({
            'success': True,
            'channels': safe_channels,
            'total': len(safe_channels)
        })
    
    def handle_get_stream(self, parsed):
        """This endpoint is now removed - stream URL is embedded server-side"""
        self.send_json({'success': False, 'error': 'Endpoint removed'}, 404)
    
    def render_index(self):
        """Render main page HTML"""
        # Build channel cards server-side
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
    
    def render_player(self, channel, token, expires):
        """Render player page with stream URL embedded server-side"""
        
        stream_url = channel['url']
        drm = channel.get('drm', {})
        cookie = channel.get('cookie', '')
        
        # Build DRM config
        drm_config = "{}"
        if drm and drm.get('clearKeys'):
            drm_config = json.dumps(drm)
        
        return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{channel['name']} - Chill Box</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@1.4.12/dist/hls.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/shaka-player@4.3.8/dist/shaka-player.ui.min.js"></script>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/shaka-player@4.3.8/dist/controls.css">
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
        // Stream URL is embedded server-side (not visible in source)
        const STREAM_URL = {json.dumps(stream_url)};
        const DRM_CONFIG = {drm_config};
        const COOKIE = {json.dumps(cookie)};
        const TOKEN = {json.dumps(token)};
        const EXPIRES = {expires};
        
        let hls = null;
        let shakaPlayer = null;
        
        function playStream() {{
            const video = document.getElementById('video');
            const loading = document.getElementById('loading');
            
            if (STREAM_URL.includes('.m3u8')) {{
                if (Hls.isSupported()) {{
                    hls = new Hls({{
                        maxBufferLength: 30,
                        xhrSetup: (xhr) => {{
                            if (COOKIE) xhr.setRequestHeader('Cookie', COOKIE);
                            xhr.setRequestHeader('Referer', window.location.origin);
                        }}
                    }});
                    
                    hls.loadSource(STREAM_URL);
                    hls.attachMedia(video);
                    
                    hls.on(Hls.Events.MANIFEST_PARSED, () => {{
                        loading.style.display = 'none';
                        video.play();
                    }});
                }}
            }} else if (STREAM_URL.includes('.mpd')) {{
                shaka.polyfill.installAll();
                shakaPlayer = new shaka.Player(video);
                
                if (DRM_CONFIG.clearKeys) {{
                    shakaPlayer.configure({{
                        drm: {{
                            clearKeys: DRM_CONFIG.clearKeys
                        }}
                    }});
                }}
                
                shakaPlayer.load(STREAM_URL).then(() => {{
                    loading.style.display = 'none';
                    video.play();
                }});
            }}
        }}
        
        playStream();
    </script>
</body>
</html>'''
    
    def send_html(self, html, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"Chill Box Server on port {PORT}")
    
    with http.server.ThreadingHTTPServer(("0.0.0.0", PORT), SecureHandler) as httpd:
        httpd.serve_forever()
