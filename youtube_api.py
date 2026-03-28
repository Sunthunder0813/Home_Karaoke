
from flask import Blueprint, request, jsonify
import os
import requests

bp = Blueprint('youtube', __name__)

YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY')

@bp.route('/youtube_search', methods=['GET'])
def youtube_search():
    query = request.args.get('q', '')
    if not query or not YOUTUBE_API_KEY:
        return jsonify({'results': []})
    url = (
        'https://www.googleapis.com/youtube/v3/search?'
        f'part=snippet&type=video&maxResults=20&q={requests.utils.quote(query)}'
        f'&videoEmbeddable=true&key={YOUTUBE_API_KEY}'
    )
    try:
        resp = requests.get(url)
        if resp.status_code != 200:
            print(f"YouTube API error: {resp.status_code} {resp.text}")
            return jsonify({'results': [], 'error': f'YouTube API error: {resp.status_code}'}), 502
        data = resp.json()
        results = []
        for item in data.get('items', []):
            # Only process if 'videoId' exists (skip channels/playlists)
            video_id = item.get('id', {}).get('videoId')
            if not video_id:
                continue
            snippet = item['snippet']
            channel = snippet['channelTitle']
            results.append({
                'id': video_id,
                'title': snippet['title'],
                'channel': channel,
                'thumbnail': snippet['thumbnails']['default']['url'],
                'duration': '',
                'link': f'https://www.youtube.com/watch?v={video_id}'
            })
        return jsonify({'results': results})
    except Exception as e:
        print(f"YouTube API exception: {e}")
        return jsonify({'results': [], 'error': str(e)}), 500
