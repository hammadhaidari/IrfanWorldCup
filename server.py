import os
import subprocess
import threading
import time
import glob
from flask import Flask, Response, send_from_directory

app = Flask(__name__)

HLS_DIR = '/tmp/hls'
STREAM_URL = 'https://bia-cf.live.pv-cdn.net/iad-nitro/live/dash/enc/w0rehjjrwe/out/v1/69a2a7041395406b970598f61680e7cf/cenc.mpd'
DECRYPTION_KEY = '17d2ac8dbc5429bd70af3433aa12158d'

ffmpeg_process = None
ffmpeg_lock = threading.Lock()


def start_ffmpeg():
    """Start ffmpeg to pull encrypted DASH, decrypt with ClearKey, output as HLS."""
    global ffmpeg_process
    with ffmpeg_lock:
        if ffmpeg_process and ffmpeg_process.poll() is None:
            return  # Already running

        os.makedirs(HLS_DIR, exist_ok=True)
        for f in glob.glob(os.path.join(HLS_DIR, '*')):
            try:
                os.remove(f)
            except OSError:
                pass

        cmd = [
            'ffmpeg', '-y',
            '-decryption_key', DECRYPTION_KEY,
            '-i', STREAM_URL,
            '-map', '0:v:0', '-map', '0:a:0',
            '-c:v', 'copy',
            '-c:a', 'aac', '-b:a', '128k',  # Convert audio to clean AAC for broad compatibility
            '-f', 'hls',
            '-hls_time', '4',
            '-hls_list_size', '10',
            '-hls_flags', 'delete_segments+append_list',
            '-hls_segment_filename', os.path.join(HLS_DIR, 'seg_%05d.ts'),
            os.path.join(HLS_DIR, 'live.m3u8')
        ]

        ffmpeg_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
        print(f'[STREAM] ffmpeg started (PID: {ffmpeg_process.pid})')


def monitor_ffmpeg():
    """Background thread: restart ffmpeg if it dies."""
    while True:
        time.sleep(10)
        if ffmpeg_process and ffmpeg_process.poll() is not None:
            print('[STREAM] ffmpeg died, restarting...')
            start_ffmpeg()


def add_cors(response):
    """Add CORS headers so the stream can be played from anywhere."""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = '*'
    return response


app.after_request(add_cors)


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/live.m3u8')
def serve_manifest():
    start_ffmpeg()
    m3u8_path = os.path.join(HLS_DIR, 'live.m3u8')

    # Wait up to 30s for ffmpeg to produce the first manifest
    for _ in range(30):
        if os.path.exists(m3u8_path) and os.path.getsize(m3u8_path) > 10:
            resp = send_from_directory(
                HLS_DIR, 'live.m3u8',
                mimetype='application/vnd.apple.mpegurl'
            )
            resp.headers['Cache-Control'] = 'no-cache, no-store'
            return resp
        time.sleep(1)

    return Response('Stream is starting up, please refresh in a few seconds...', status=503)


@app.route('/seg_<path:filename>')
def serve_segment(filename):
    seg_name = 'seg_' + filename
    seg_path = os.path.join(HLS_DIR, seg_name)
    if os.path.exists(seg_path):
        return send_from_directory(HLS_DIR, seg_name, mimetype='video/MP2T')
    return Response('Segment not found', status=404)


@app.route('/health')
def health():
    running = ffmpeg_process and ffmpeg_process.poll() is None
    return {'status': 'ok', 'ffmpeg_running': running}


# Start ffmpeg and monitor thread on import
start_ffmpeg()
monitor_thread = threading.Thread(target=monitor_ffmpeg, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
