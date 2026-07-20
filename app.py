from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
load_dotenv()
import os
import re
from youtube_api import bp as youtube_bp

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-fallback-key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///karaoke.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.register_blueprint(youtube_bp)

db = SQLAlchemy(app)


# ── Models ────────────────────────────────────────────────────────────────────

class Song(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    artist = db.Column(db.String(200), nullable=False)
    genre = db.Column(db.String(50), default='Pop')


class QueueItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.String(20), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    channel = db.Column(db.String(200), default='')


# ── Queue helpers (DB-backed, shared across all clients) ──────────────────────

def get_queue():
    items = QueueItem.query.order_by(QueueItem.id).all()
    return [{'video_id': i.video_id, 'title': i.title, 'channel': i.channel} for i in items]


def extract_video_id(url: str):
    patterns = [
        r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})',
        r'(?:embed/)([A-Za-z0-9_-]{11})',
        r'^([A-Za-z0-9_-]{11})$',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """Main karaoke TV screen — just plays videos, no controls."""
    queue = get_queue()
    return render_template('index.html', queue=queue)


@app.route('/search')
def search_page():
    """Remote control page — search, songbook, queue management."""
    songs = Song.query.all()
    genres = sorted(set(s.genre for s in songs if s.genre))
    queue = get_queue()
    return render_template('search.html', genres=genres, queue=queue)


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.route('/api/queue')
def api_queue():
    return jsonify({'queue': get_queue()})


@app.route('/songlist')
def songlist():
    q = request.args.get('q', '').strip().lower()
    genre = request.args.get('genre', '').strip()
    query = Song.query
    if q:
        query = query.filter(
            db.or_(Song.title.ilike(f'%{q}%'), Song.artist.ilike(f'%{q}%'))
        )
    if genre:
        query = query.filter(Song.genre == genre)
    songs = query.order_by(Song.artist, Song.title).all()
    return jsonify({
        'songs': [{'id': s.id, 'title': s.title, 'artist': s.artist, 'genre': s.genre} for s in songs]
    })


@app.route('/add_to_queue', methods=['POST'])
def add_to_queue():
    video_id = request.form.get('video_id')
    title = request.form.get('title')
    channel = request.form.get('channel', '')
    if video_id and title:
        db.session.add(QueueItem(video_id=video_id, title=title, channel=channel))
        db.session.commit()
        return jsonify({'success': True, 'queue': get_queue()})
    return jsonify({'success': False})


@app.route('/add_link_to_queue', methods=['POST'])
def add_link_to_queue():
    link = request.form.get('youtube_link', '').strip()
    video_id = extract_video_id(link)
    if not video_id:
        return jsonify({'success': False, 'error': 'Invalid link'})

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
        embeddable = status.get('embeddable', False)
        if not embeddable:
            return jsonify({'success': False, 'error': 'Video not embeddable'})
        title = snippet.get('title', title)
        channel = snippet.get('channelTitle', channel)
    except Exception:
        return jsonify({'success': False, 'error': 'Failed to check video'})

    db.session.add(QueueItem(video_id=video_id, title=title, channel=channel))
    db.session.commit()
    return jsonify({'success': True, 'queue': get_queue()})


@app.route('/remove_from_queue', methods=['POST'])
def remove_from_queue():
    idx = int(request.form.get('idx', -1))
    items = QueueItem.query.order_by(QueueItem.id).all()
    if 0 <= idx < len(items):
        db.session.delete(items[idx])
        db.session.commit()
        return jsonify({'success': True, 'queue': get_queue()})
    return jsonify({'success': False})


@app.route('/skip_song', methods=['POST'])
def skip_song():
    """Remove the first song (called by the TV screen to auto-advance)."""
    first = QueueItem.query.order_by(QueueItem.id).first()
    if first:
        db.session.delete(first)
        db.session.commit()
    return jsonify({'success': True, 'queue': get_queue()})


@app.route('/clear_queue', methods=['POST'])
def clear_queue():
    QueueItem.query.delete()
    db.session.commit()
    return jsonify({'success': True, 'queue': []})


# ── Seed ──────────────────────────────────────────────────────────────────────

def seed_songlist():
    if Song.query.first():
        return

    songs = [
        # ── Pop ──
        ('Bohemian Rhapsody', 'Queen', 'Pop'),
        ("Don't Stop Believin'", 'Journey', 'Pop'),
        ('Sweet Caroline', 'Neil Diamond', 'Pop'),
        ("Livin' on a Prayer", 'Bon Jovi', 'Pop'),
        ('I Will Always Love You', 'Whitney Houston', 'Pop'),
        ('Total Eclipse of the Heart', 'Bonnie Tyler', 'Pop'),
        ('Take On Me', 'a-ha', 'Pop'),
        ('Somebody That I Used to Know', 'Gotye', 'Pop'),
        ('Rolling in the Deep', 'Adele', 'Pop'),
        ('Someone Like You', 'Adele', 'Pop'),
        ('Set Fire to the Rain', 'Adele', 'Pop'),
        ('Hello', 'Adele', 'Pop'),
        ('Shape of You', 'Ed Sheeran', 'Pop'),
        ('Thinking Out Loud', 'Ed Sheeran', 'Pop'),
        ('Photograph', 'Ed Sheeran', 'Pop'),
        ('Uptown Funk', 'Bruno Mars', 'Pop'),
        ('Just the Way You Are', 'Bruno Mars', 'Pop'),
        ('Grenade', 'Bruno Mars', 'Pop'),
        ('24K Magic', 'Bruno Mars', 'Pop'),
        ('Treasure', 'Bruno Mars', 'Pop'),
        ('Bad Romance', 'Lady Gaga', 'Pop'),
        ('Poker Face', 'Lady Gaga', 'Pop'),
        ('Born This Way', 'Lady Gaga', 'Pop'),
        ('Blinding Lights', 'The Weeknd', 'Pop'),
        ('Save Your Tears', 'The Weeknd', 'Pop'),
        ('Levitating', 'Dua Lipa', 'Pop'),
        ("Don't Start Now", 'Dua Lipa', 'Pop'),
        ('Watermelon Sugar', 'Harry Styles', 'Pop'),
        ('As It Was', 'Harry Styles', 'Pop'),
        ('Anti-Hero', 'Taylor Swift', 'Pop'),
        ('Shake It Off', 'Taylor Swift', 'Pop'),
        ('Love Story', 'Taylor Swift', 'Pop'),
        ('Blank Space', 'Taylor Swift', 'Pop'),
        ('Cruel Summer', 'Taylor Swift', 'Pop'),
        ('You Belong with Me', 'Taylor Swift', 'Pop'),
        ('Flowers', 'Miley Cyrus', 'Pop'),
        ('Wrecking Ball', 'Miley Cyrus', 'Pop'),
        ('Happy', 'Pharrell Williams', 'Pop'),
        ('Counting Stars', 'OneRepublic', 'Pop'),
        ('Viva La Vida', 'Coldplay', 'Pop'),
        ('Yellow', 'Coldplay', 'Pop'),
        ('Fix You', 'Coldplay', 'Pop'),
        ('The Scientist', 'Coldplay', 'Pop'),
        ('Mr. Brightside', 'The Killers', 'Pop'),
        ('Hey Jude', 'The Beatles', 'Pop'),
        ('Let It Be', 'The Beatles', 'Pop'),
        ('Yesterday', 'The Beatles', 'Pop'),
        ('Here Comes the Sun', 'The Beatles', 'Pop'),
        ('Wonderwall', 'Oasis', 'Pop'),
        ('Dancing Queen', 'ABBA', 'Pop'),
        ('Mamma Mia', 'ABBA', 'Pop'),
        ('Waterloo', 'ABBA', 'Pop'),
        ('Roar', 'Katy Perry', 'Pop'),
        ('Firework', 'Katy Perry', 'Pop'),
        ('Teenage Dream', 'Katy Perry', 'Pop'),
        ('Call Me Maybe', 'Carly Rae Jepsen', 'Pop'),
        ('Girls Just Want to Have Fun', 'Cyndi Lauper', 'Pop'),
        ('Like a Virgin', 'Madonna', 'Pop'),
        ('Material Girl', 'Madonna', 'Pop'),
        ('Wannabe', 'Spice Girls', 'Pop'),
        ('No Scrubs', 'TLC', 'Pop'),
        ('Toxic', 'Britney Spears', 'Pop'),
        ('Baby One More Time', 'Britney Spears', 'Pop'),
        ('Stronger', 'Kelly Clarkson', 'Pop'),
        ('Since U Been Gone', 'Kelly Clarkson', 'Pop'),
        ('Chandelier', 'Sia', 'Pop'),
        ('Cheap Thrills', 'Sia', 'Pop'),
        ('Havana', 'Camila Cabello', 'Pop'),
        ("Say You Won't Let Go", 'James Arthur', 'Pop'),
        ('Stitches', 'Shawn Mendes', 'Pop'),
        ("There's Nothing Holdin' Me Back", 'Shawn Mendes', 'Pop'),
        ('Stay', 'The Kid LAROI & Justin Bieber', 'Pop'),
        ('Peaches', 'Justin Bieber', 'Pop'),
        ('Sorry', 'Justin Bieber', 'Pop'),
        ('Love Yourself', 'Justin Bieber', 'Pop'),
        ('drivers license', 'Olivia Rodrigo', 'Pop'),
        ('good 4 u', 'Olivia Rodrigo', 'Pop'),
        ('vampire', 'Olivia Rodrigo', 'Pop'),
        ('Attention', 'Charlie Puth', 'Pop'),
        ('We Found Love', 'Rihanna', 'Pop'),
        ('Umbrella', 'Rihanna', 'Pop'),
        ('Diamonds', 'Rihanna', 'Pop'),

        # ── R&B / Soul ──
        ('No One', 'Alicia Keys', 'R&B'),
        ("If I Ain't Got You", 'Alicia Keys', 'R&B'),
        ("Fallin'", 'Alicia Keys', 'R&B'),
        ("Ain't No Sunshine", 'Bill Withers', 'R&B'),
        ('Lean on Me', 'Bill Withers', 'R&B'),
        ('Halo', 'Beyoncé', 'R&B'),
        ('Crazy in Love', 'Beyoncé', 'R&B'),
        ('Love on Top', 'Beyoncé', 'R&B'),
        ('Single Ladies', 'Beyoncé', 'R&B'),
        ('Irreplaceable', 'Beyoncé', 'R&B'),
        ('Stay With Me', 'Sam Smith', 'R&B'),
        ("I'm Not the Only One", 'Sam Smith', 'R&B'),
        ('All of Me', 'John Legend', 'R&B'),
        ('Ordinary People', 'John Legend', 'R&B'),
        ("Let's Stay Together", 'Al Green', 'R&B'),
        ('Superstition', 'Stevie Wonder', 'R&B'),
        ("Isn't She Lovely", 'Stevie Wonder', 'R&B'),
        ('I Just Called to Say I Love You', 'Stevie Wonder', 'R&B'),
        ('I Will Survive', 'Gloria Gaynor', 'R&B'),
        ('Respect', 'Aretha Franklin', 'R&B'),
        ('Natural Woman', 'Aretha Franklin', 'R&B'),
        ('Killing Me Softly', 'Fugees', 'R&B'),
        ("No Diggity", 'Blackstreet', 'R&B'),
        ('End of the Road', 'Boyz II Men', 'R&B'),
        ("I'll Make Love to You", 'Boyz II Men', 'R&B'),
        ('Waterfalls', 'TLC', 'R&B'),
        ('Kiss from a Rose', 'Seal', 'R&B'),
        ("Let's Get It On", 'Marvin Gaye', 'R&B'),
        ('Sexual Healing', 'Marvin Gaye', 'R&B'),
        ("I Heard It Through the Grapevine", 'Marvin Gaye', 'R&B'),

        # ── Rock ──
        ('Hotel California', 'Eagles', 'Rock'),
        ('Stairway to Heaven', 'Led Zeppelin', 'Rock'),
        ('Sweet Home Alabama', 'Lynyrd Skynyrd', 'Rock'),
        ('Free Bird', 'Lynyrd Skynyrd', 'Rock'),
        ('We Will Rock You', 'Queen', 'Rock'),
        ('We Are the Champions', 'Queen', 'Rock'),
        ('Somebody to Love', 'Queen', 'Rock'),
        ('Dream On', 'Aerosmith', 'Rock'),
        ("Summer of '69", 'Bryan Adams', 'Rock'),
        ('Heaven', 'Bryan Adams', 'Rock'),
        ('Everything I Do', 'Bryan Adams', 'Rock'),
        ('Under the Bridge', 'Red Hot Chili Peppers', 'Rock'),
        ('Californication', 'Red Hot Chili Peppers', 'Rock'),
        ('Smells Like Teen Spirit', 'Nirvana', 'Rock'),
        ('Creep', 'Radiohead', 'Rock'),
        ('Nothing Else Matters', 'Metallica', 'Rock'),
        ('Enter Sandman', 'Metallica', 'Rock'),
        ('Back in Black', 'AC/DC', 'Rock'),
        ('Highway to Hell', 'AC/DC', 'Rock'),
        ('Thunderstruck', 'AC/DC', 'Rock'),
        ("Sweet Child O' Mine", "Guns N' Roses", 'Rock'),
        ('November Rain', "Guns N' Roses", 'Rock'),
        ('Paradise City', "Guns N' Roses", 'Rock'),
        ("Livin' on a Prayer", 'Bon Jovi', 'Rock'),
        ("It's My Life", 'Bon Jovi', 'Rock'),
        ('You Give Love a Bad Name', 'Bon Jovi', 'Rock'),
        ('More Than a Feeling', 'Boston', 'Rock'),
        ('Fortunate Son', 'Creedence Clearwater Revival', 'Rock'),
        ('Paint It Black', 'The Rolling Stones', 'Rock'),
        ('Satisfaction', 'The Rolling Stones', 'Rock'),
        ('Born to Run', 'Bruce Springsteen', 'Rock'),
        ('Eye of the Tiger', 'Survivor', 'Rock'),
        ('Africa', 'Toto', 'Rock'),
        ("Don't Stop Me Now", 'Queen', 'Rock'),

        # ── Country ──
        ('Jolene', 'Dolly Parton', 'Country'),
        ('9 to 5', 'Dolly Parton', 'Country'),
        ('Friends in Low Places', 'Garth Brooks', 'Country'),
        ('The Dance', 'Garth Brooks', 'Country'),
        ('Ring of Fire', 'Johnny Cash', 'Country'),
        ('Folsom Prison Blues', 'Johnny Cash', 'Country'),
        ('I Walk the Line', 'Johnny Cash', 'Country'),
        ('Take Me Home, Country Roads', 'John Denver', 'Country'),
        ('Rocky Mountain High', 'John Denver', 'Country'),
        ('Wagon Wheel', 'Darius Rucker', 'Country'),
        ('Before He Cheats', 'Carrie Underwood', 'Country'),
        ('Crazy', 'Patsy Cline', 'Country'),
        ('Stand By Your Man', 'Tammy Wynette', 'Country'),
        ("Boot Scootin' Boogie", 'Brooks & Dunn', 'Country'),
        ('Achy Breaky Heart', 'Billy Ray Cyrus', 'Country'),
        ("Your Cheatin' Heart", 'Hank Williams', 'Country'),
        ('The Gambler', 'Kenny Rogers', 'Country'),
        ('Islands in the Stream', 'Dolly Parton & Kenny Rogers', 'Country'),
        ('Cruise', 'Florida Georgia Line', 'Country'),
        ('Body Like a Back Road', 'Sam Hunt', 'Country'),
        ('Tequila', 'Dan + Shay', 'Country'),
        ('Die a Happy Man', 'Thomas Rhett', 'Country'),

        # ── OPM (Original Pilipino Music) ──
        ('Nandito Ako', 'Ogie Alcasid', 'OPM'),
        ('Ikaw', 'Yeng Constantino', 'OPM'),
        ('Hanggang', 'Wency Cornejo', 'OPM'),
        ('Anak', 'Freddie Aguilar', 'OPM'),
        ('Harana', 'Parokya ni Edgar', 'OPM'),
        ('Inuman Na', 'Parokya ni Edgar', 'OPM'),
        ('Buloy', 'Parokya ni Edgar', 'OPM'),
        ('Tadhana', 'Up Dharma Down', 'OPM'),
        ('Oo', 'Up Dharma Down', 'OPM'),
        ('Mundo', 'IV of Spades', 'OPM'),
        ('Bawat Kaluluwa', 'IV of Spades', 'OPM'),
        ('Ang Huling El Bimbo', 'Eraserheads', 'OPM'),
        ('With a Smile', 'Eraserheads', 'OPM'),
        ('Ligaya', 'Eraserheads', 'OPM'),
        ('Pare Ko', 'Eraserheads', 'OPM'),
        ('Alapaap', 'Eraserheads', 'OPM'),
        ('Magasin', 'Eraserheads', 'OPM'),
        ('Huling El Bimbo', 'Eraserheads', 'OPM'),
        ('Minsan', 'Eraserheads', 'OPM'),
        ('Spolarium', 'Eraserheads', 'OPM'),
        ('Torete', 'Moonstar88', 'OPM'),
        ('Migraine', 'Moonstar88', 'OPM'),
        ('Sulat', 'Moonstar88', 'OPM'),
        ('Huwag Ka Nang Umiyak', 'Sugarfree', 'OPM'),
        ('Burnout', 'Sugarfree', 'OPM'),
        ('Mariposa', 'Sugarfree', 'OPM'),
        ('Liwanag Sa Dilim', 'Rivermaya', 'OPM'),
        ('214', 'Rivermaya', 'OPM'),
        ('Kisapmata', 'Rivermaya', 'OPM'),
        ('Elesi', 'Rivermaya', 'OPM'),
        ('Himala', 'Rivermaya', 'OPM'),
        ('Kahit Maputi Na Ang Buhok Ko', 'Noel Cabangon', 'OPM'),
        ('Kanlungan', 'Noel Cabangon', 'OPM'),
        ('Sana Maulit Muli', 'Gary Valenciano', 'OPM'),
        ('Di Bale Na Lang', 'Gary Valenciano', 'OPM'),
        ('Natutulog Ba Ang Diyos', 'Gary Valenciano', 'OPM'),
        ('Di Na Mababawi', 'Sponge Cola', 'OPM'),
        ('Kay Tagal Kitang Hinintay', 'Sponge Cola', 'OPM'),
        ('Jeepney', 'Sponge Cola', 'OPM'),
        ('Dito Ka Lang', 'Moira Dela Torre', 'OPM'),
        ('Malaya', 'Moira Dela Torre', 'OPM'),
        ('Paubaya', 'Moira Dela Torre', 'OPM'),
        ('Tagpuan', 'Moira Dela Torre', 'OPM'),
        ('Buwan', 'Juan Karlos', 'OPM'),
        ('Ere', 'Juan Karlos', 'OPM'),
        ('Dying Inside to Hold You', 'Darren Espanto', 'OPM'),
        ('Maybe the Night', 'Ben&Ben', 'OPM'),
        ('Leaves', 'Ben&Ben', 'OPM'),
        ('Pagtingin', 'Ben&Ben', 'OPM'),
        ('Araw-Araw', 'Ben&Ben', 'OPM'),
        ('Kathang Isip', 'Ben&Ben', 'OPM'),
        ('Ride Home', 'Ben&Ben', 'OPM'),
        ('Malibu Nights', 'Ben&Ben', 'OPM'),
        ('Sa Susunod Na Habang Buhay', 'Ben&Ben', 'OPM'),
        ('Ikaw Lang', 'NOBITA', 'OPM'),
        ('Ikaw at Ako', 'TJ Monterde', 'OPM'),
        ('Kung Di Rin Lang Ikaw', 'December Avenue', 'OPM'),
        ('Sa Ngalan Ng Pag-Ibig', 'December Avenue', 'OPM'),
        ('Bulong', 'December Avenue', 'OPM'),
        ('Dahan', 'December Avenue', 'OPM'),
        ('Kilometro', 'Sarah Geronimo', 'OPM'),
        ('Tala', 'Sarah Geronimo', 'OPM'),
        ("Pangako Sa 'Yo", 'Vina Morales', 'OPM'),
        ('Ikaw Lamang', 'Silent Sanctuary', 'OPM'),
        ('Pasensya Ka Na', 'Silent Sanctuary', 'OPM'),
        ('Hinahanap-Hanap Kita', 'Rivermaya', 'OPM'),
        ('Bakit Ba Ikaw', 'Michael Pangilinan', 'OPM'),
        ('Your Song (One and Only You)', 'Parokya ni Edgar', 'OPM'),
        ('Hawak Kamay', 'Yeng Constantino', 'OPM'),
        ('Salamat', 'Yeng Constantino', 'OPM'),
        ('Wag Ka Nang Umiyak', 'Gary Valenciano', 'OPM'),
        ('Pusong Ligaw', 'Jericho Rosales', 'OPM'),
        ('Naaalala Ka', 'Rey Valera', 'OPM'),
        ('Kung Kailangan Mo Ako', 'Rey Valera', 'OPM'),
        ('Awitin Mo At Isasayaw Ko', 'VST & Company', 'OPM'),
        ('Nais Ko', 'Basil Valdez', 'OPM'),
        ('Kastilyong Buhangin', 'Basil Valdez', 'OPM'),

        # ── Ballad ──
        ('My Heart Will Go On', 'Celine Dion', 'Ballad'),
        ('The Power of Love', 'Celine Dion', 'Ballad'),
        ('Because You Loved Me', 'Celine Dion', 'Ballad'),
        ('All By Myself', 'Celine Dion', 'Ballad'),
        ("It's All Coming Back to Me Now", 'Celine Dion', 'Ballad'),
        ("I Don't Want to Miss a Thing", 'Aerosmith', 'Ballad'),
        ('Unchained Melody', 'Righteous Brothers', 'Ballad'),
        ('Endless Love', 'Diana Ross & Lionel Richie', 'Ballad'),
        ('A Thousand Years', 'Christina Perri', 'Ballad'),
        ('Perfect', 'Ed Sheeran', 'Ballad'),
        ("Can't Help Falling in Love", 'Elvis Presley', 'Ballad'),
        ('Always on My Mind', 'Elvis Presley', 'Ballad'),
        ('Love Me Tender', 'Elvis Presley', 'Ballad'),
        ('At Last', 'Etta James', 'Ballad'),
        ('Make You Feel My Love', 'Adele', 'Ballad'),
        ('Say Something', 'A Great Big World', 'Ballad'),
        ('When I Was Your Man', 'Bruno Mars', 'Ballad'),
        ('All of Me', 'John Legend', 'Ballad'),
        ('You Are So Beautiful', 'Joe Cocker', 'Ballad'),
        ('Wind Beneath My Wings', 'Bette Midler', 'Ballad'),
        ('The Rose', 'Bette Midler', 'Ballad'),
        ('Time After Time', 'Cyndi Lauper', 'Ballad'),
        ('True Colors', 'Cyndi Lauper', 'Ballad'),
        ('Against All Odds', 'Phil Collins', 'Ballad'),
        ('In the Air Tonight', 'Phil Collins', 'Ballad'),
        ('Right Here Waiting', 'Richard Marx', 'Ballad'),
        ('Open Arms', 'Journey', 'Ballad'),
        ('Faithfully', 'Journey', 'Ballad'),
        ('Power of Love', 'Huey Lewis & The News', 'Ballad'),
        ('Every Breath You Take', 'The Police', 'Ballad'),
        ('Careless Whisper', 'George Michael', 'Ballad'),
        ('I Want to Know What Love Is', 'Foreigner', 'Ballad'),
        ('Eternal Flame', 'The Bangles', 'Ballad'),
        ("I'll Be There", 'Jackson 5', 'Ballad'),
        ('How Deep Is Your Love', 'Bee Gees', 'Ballad'),

        # ── Dance / Party ──
        ('September', 'Earth, Wind & Fire', 'Dance'),
        ("Stayin' Alive", 'Bee Gees', 'Dance'),
        ('I Gotta Feeling', 'Black Eyed Peas', 'Dance'),
        ('Party in the U.S.A.', 'Miley Cyrus', 'Dance'),
        ("Livin' La Vida Loca", 'Ricky Martin', 'Dance'),
        ('Shut Up and Dance', 'Walk the Moon', 'Dance'),
        ('Timber', 'Pitbull ft. Ke$ha', 'Dance'),
        ('Yeah!', 'Usher', 'Dance'),
        ('Get Lucky', 'Daft Punk', 'Dance'),
        ('Dynamite', 'BTS', 'Dance'),
        ('Butter', 'BTS', 'Dance'),
        ('Boy With Luv', 'BTS', 'Dance'),
        ('Ice Cream', 'BLACKPINK', 'Dance'),
        ('How You Like That', 'BLACKPINK', 'Dance'),
        ('Gangnam Style', 'PSY', 'Dance'),
        ("Can't Stop the Feeling", 'Justin Timberlake', 'Dance'),
        ('SexyBack', 'Justin Timberlake', 'Dance'),
        ('Moves Like Jagger', 'Maroon 5', 'Dance'),
        ('Sugar', 'Maroon 5', 'Dance'),
        ('Payphone', 'Maroon 5', 'Dance'),
        ('This Love', 'Maroon 5', 'Dance'),
        ('Raise Your Glass', 'P!nk', 'Dance'),
        ('So What', 'P!nk', 'Dance'),
        ('Uptown Girl', 'Billy Joel', 'Dance'),
        ('Piano Man', 'Billy Joel', 'Dance'),
        ("Livin' on a Prayer", 'Bon Jovi', 'Dance'),
        ('YMCA', 'Village People', 'Dance'),
        ('Macarena', 'Los Del Rio', 'Dance'),
        ("Ain't Nobody", 'Chaka Khan', 'Dance'),
        ('Le Freak', 'Chic', 'Dance'),

        # ── Duet ──
        ('A Whole New World', 'Aladdin Soundtrack', 'Duet'),
        ("Don't Go Breaking My Heart", 'Elton John & Kiki Dee', 'Duet'),
        ('Summer Nights', 'Grease Soundtrack', 'Duet'),
        ("You're the One That I Want", 'Grease Soundtrack', 'Duet'),
        ('Rewrite the Stars', 'The Greatest Showman', 'Duet'),
        ('Shallow', 'Lady Gaga & Bradley Cooper', 'Duet'),
        ('Island in the Stream', 'Dolly Parton & Kenny Rogers', 'Duet'),
        ("Ain't No Mountain High Enough", 'Marvin Gaye & Tammi Terrell', 'Duet'),
        ('Endless Love', 'Diana Ross & Lionel Richie', 'Duet'),
        ('Under Pressure', 'Queen & David Bowie', 'Duet'),
        ("Somethin' Stupid", 'Frank & Nancy Sinatra', 'Duet'),
        ('I Got You Babe', 'Sonny & Cher', 'Duet'),
        ('The Prayer', 'Andrea Bocelli & Celine Dion', 'Duet'),
        ('Time of My Life', 'Dirty Dancing Soundtrack', 'Duet'),
        ('Unforgettable', 'Nat King Cole & Natalie Cole', 'Duet'),
        ('Up Where We Belong', 'Joe Cocker & Jennifer Warnes', 'Duet'),
        ('Beauty and the Beast', 'Angela Lansbury', 'Duet'),
        ('Can You Feel the Love Tonight', 'Elton John', 'Duet'),
        ("You Don't Bring Me Flowers", 'Barbra Streisand & Neil Diamond', 'Duet'),
        ('Nobody Wants to Be Lonely', 'Ricky Martin & Christina Aguilera', 'Duet'),

        # ── 90s / 2000s Throwback ──
        ('Wonderwall', 'Oasis', '90s/2000s'),
        ('MMMBop', 'Hanson', '90s/2000s'),
        ('Iris', 'Goo Goo Dolls', '90s/2000s'),
        ('Semi-Charmed Life', 'Third Eye Blind', '90s/2000s'),
        ('Closing Time', 'Semisonic', '90s/2000s'),
        ('Torn', 'Natalie Imbruglia', '90s/2000s'),
        ('Zombie', 'The Cranberries', '90s/2000s'),
        ('Kiss Me', 'Sixpence None the Richer', '90s/2000s'),
        ('Genie in a Bottle', 'Christina Aguilera', '90s/2000s'),
        ('Beautiful', 'Christina Aguilera', '90s/2000s'),
        ("Livin' La Vida Loca", 'Ricky Martin', '90s/2000s'),
        ('Blue (Da Ba Dee)', 'Eiffel 65', '90s/2000s'),
        ('Barbie Girl', 'Aqua', '90s/2000s'),
        ('Bye Bye Bye', 'NSYNC', '90s/2000s'),
        ('I Want It That Way', 'Backstreet Boys', '90s/2000s'),
        ("Everybody (Backstreet's Back)", 'Backstreet Boys', '90s/2000s'),
        ('As Long As You Love Me', 'Backstreet Boys', '90s/2000s'),
        ('Larger Than Life', 'Backstreet Boys', '90s/2000s'),
        ('Smooth', 'Santana ft. Rob Thomas', '90s/2000s'),
        ('Unbreak My Heart', 'Toni Braxton', '90s/2000s'),
        ('My Immortal', 'Evanescence', '90s/2000s'),
        ('Bring Me to Life', 'Evanescence', '90s/2000s'),
        ('Complicated', 'Avril Lavigne', '90s/2000s'),
        ('Sk8er Boi', 'Avril Lavigne', '90s/2000s'),
        ('Beautiful Day', 'U2', '90s/2000s'),
        ('With or Without You', 'U2', '90s/2000s'),
        ('How to Save a Life', 'The Fray', '90s/2000s'),
        ('Chasing Cars', 'Snow Patrol', '90s/2000s'),
        ('Hey Ya!', 'OutKast', '90s/2000s'),
        ('In the End', 'Linkin Park', '90s/2000s'),
        ('Numb', 'Linkin Park', '90s/2000s'),

        # ── Disney / Soundtrack ──
        ('Let It Go', 'Frozen Soundtrack', 'Disney'),
        ('A Whole New World', 'Aladdin Soundtrack', 'Disney'),
        ('Under the Sea', 'The Little Mermaid', 'Disney'),
        ('Part of Your World', 'The Little Mermaid', 'Disney'),
        ('Hakuna Matata', 'The Lion King', 'Disney'),
        ('Circle of Life', 'The Lion King', 'Disney'),
        ('Can You Feel the Love Tonight', 'The Lion King', 'Disney'),
        ('Beauty and the Beast', 'Beauty and the Beast', 'Disney'),
        ("You've Got a Friend in Me", 'Toy Story', 'Disney'),
        ('Colors of the Wind', 'Pocahontas', 'Disney'),
        ('Reflection', 'Mulan Soundtrack', 'Disney'),
        ("How Far I'll Go", 'Moana Soundtrack', 'Disney'),
        ("We Don't Talk About Bruno", 'Encanto', 'Disney'),
        ('Surface Pressure', 'Encanto', 'Disney'),
        ('Into the Unknown', 'Frozen 2', 'Disney'),
        ('Remember Me', 'Coco Soundtrack', 'Disney'),
        ('Rewrite the Stars', 'The Greatest Showman', 'Disney'),
        ('This Is Me', 'The Greatest Showman', 'Disney'),
        ('A Million Dreams', 'The Greatest Showman', 'Disney'),
        ('Defying Gravity', 'Wicked', 'Disney'),
    ]

    for title, artist, genre in songs:
        db.session.add(Song(title=title, artist=artist, genre=genre))
    db.session.commit()
    print(f'Seeded {len(songs)} songs into songbook.')


# ── Init DB at startup (works with gunicorn, not just __main__) ───────────────

with app.app_context():
    db.create_all()
    seed_songlist()


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')