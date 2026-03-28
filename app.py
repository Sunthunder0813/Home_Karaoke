from flask import Flask, render_template, redirect, url_for, request, flash, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
load_dotenv()
import os
import re
from youtube_api import bp as youtube_bp

app = Flask(__name__)
app.config['SECRET_KEY'] = 'replace-this-with-a-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///karaoke.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Register YouTube API blueprint
app.register_blueprint(youtube_bp)

db = SQLAlchemy(app)

class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    artist = db.Column(db.String(200), nullable=False)
    lyrics = db.Column(db.Text, nullable=False)
    audio_file = db.Column(db.String(200), nullable=False)


# ── Queue helpers ──────────────────────────────────────────────────────────────

def get_queue():
    return session.get('karaoke_queue', [])

def set_queue(queue):
    session['karaoke_queue'] = queue

def extract_video_id(url: str):
    """Extract YouTube video ID from a variety of URL formats."""
    patterns = [
        r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})',
        r'(?:embed/)([A-Za-z0-9_-]{11})',
        r'^([A-Za-z0-9_-]{11})$',  # bare ID
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/', methods=['GET', 'POST'])
def index():
    songs = Song.query.all()
    queue = get_queue()
    return render_template('index.html', songs=songs, queue=queue)



@app.route('/add_to_queue', methods=['POST'])
def add_to_queue():
    video_id = request.form.get('video_id')
    title = request.form.get('title')
    channel = request.form.get('channel', '')
    if video_id and title:
        queue = get_queue()
        was_empty = len(queue) == 0          # ← track before adding
        queue.append({'video_id': video_id, 'title': title, 'channel': channel})
        set_queue(queue)
        return jsonify({'success': True, 'queue': queue, 'was_empty': was_empty})
    return jsonify({'success': False})


@app.route('/add_link_to_queue', methods=['POST'])
def add_link_to_queue():
    """Add a video directly by pasting a YouTube URL."""
    link = request.form.get('youtube_link', '').strip()
    video_id = extract_video_id(link)
    if not video_id:
        return jsonify({'success': False})

    # Try to fetch title and channel via oEmbed (no API key needed)
    title = link
    channel = ''
    try:
        import requests as req
        oembed = req.get(
            f'https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json',
            timeout=4
        ).json()
        title = oembed.get('title', link)
        channel = oembed.get('author_name', '')
    except Exception:
        pass

    # Check embeddability using YouTube Data API
    import requests as req
    api_key = os.environ.get('YOUTUBE_API_KEY')
    if not api_key:
        return jsonify({'success': False, 'error': 'No API key'})
    info_url = f'https://www.googleapis.com/youtube/v3/videos?part=snippet,status&id={video_id}&key={api_key}'
    try:
        resp = req.get(info_url, timeout=5)
        if resp.status_code != 200:
            return jsonify({'success': False, 'error': 'YouTube API error'})
        data = resp.json()
        items = data.get('items', [])
        if not items:
            return jsonify({'success': False, 'error': 'Video not found'})
        item = items[0]
        snippet = item.get('snippet', {})
        status = item.get('status', {})
        channel_title = snippet.get('channelTitle', channel)
        embeddable = status.get('embeddable', False)
        if not embeddable:
            return jsonify({'success': False, 'error': 'Video not embeddable'})
        title = snippet.get('title', title)
        channel = channel_title
    except Exception:
        return jsonify({'success': False, 'error': 'Failed to check video'})

    queue = get_queue()
    was_empty = len(queue) == 0              # ← track before adding
    queue.append({'video_id': video_id, 'title': title, 'channel': channel})
    set_queue(queue)
    return jsonify({'success': True, 'was_empty': was_empty, 'queue': queue})


@app.route('/remove_from_queue', methods=['POST'])
def remove_from_queue():
    idx = int(request.form.get('idx', -1))
    queue = get_queue()
    if 0 <= idx < len(queue):
        queue.pop(idx)
        set_queue(queue)
        return jsonify({'success': True, 'queue': queue})
    return jsonify({'success': False})


@app.route('/song/<int:song_id>')
def song(song_id):
    song = Song.query.get_or_404(song_id)
    return render_template('song.html', song=song)


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        app.run(debug=True)