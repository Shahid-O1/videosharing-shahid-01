import os, datetime, uuid
from functools import wraps
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

# ---------- Config ----------
SECRET_KEY = os.getenv("SECRET_KEY", "change_me_now")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ---------- Models ----------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    pw_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="consumer")  # consumer|creator
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class Video(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    publisher = db.Column(db.String(120))
    producer = db.Column(db.String(120))
    genre = db.Column(db.String(80))
    age = db.Column(db.String(20))
    kind = db.Column(db.String(10), nullable=False, default="youtube")  # youtube|file
    youtube_id = db.Column(db.String(32))   # if kind=youtube
    file_url = db.Column(db.Text)           # if kind=file (not used yet)
    views = db.Column(db.Integer, default=0)
    likes = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    uploader_id = db.Column(db.Integer, db.ForeignKey("user.id"))

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey("video.id"), nullable=False, index=True)
    user = db.Column(db.String(80), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.Integer, db.ForeignKey("video.id"), nullable=False, index=True)
    user = db.Column(db.String(80), nullable=False)
    value = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

with app.app_context():
    db.create_all()
    # Auto-seed one video matching your HTML if DB empty
    if Video.query.count() == 0:
        db.session.add(Video(
            title="Cricket Highlights - India vs Australia",
            description="Sample cricket match highlight.",
            publisher="SportsTV",
            producer="SportsTV",
            genre="Sports",
            age="PG",
            kind="youtube",
            youtube_id="YEyWIyPfQWA",
            views=120, likes=80
        ))
        db.session.commit()

# ---------- Auth (simple, no JWT to keep it minimal) ----------
def require_role(role):
    def _decorator(fn):
        @wraps(fn)
        def _wrapped(*args, **kwargs):
            # Simple header-based auth for demo:
            # send 'X-User: <username>' and optional 'X-Role: creator/consumer'
            u = (request.headers.get("X-User") or "").strip()
            if not u:
                return jsonify({"error": "X-User header required"}), 401
            user = User.query.filter_by(username=u).first()
            if not user:
                return jsonify({"error": "unknown user"}), 401
            if role and user.role != role:
                return jsonify({"error": f"{role} role required"}), 403
            request._user = user
            return fn(*args, **kwargs)
        return _wrapped
    return _decorator

@app.post("/auth/signup")
def signup():
    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    role = (data.get("role") or "consumer").strip()
    if role not in {"consumer", "creator"} or not username or not password:
        return jsonify({"error": "username, password, role(required)"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "username taken"}), 409
    u = User(username=username, pw_hash=generate_password_hash(password), role=role)
    db.session.add(u); db.session.commit()
    return jsonify({"ok": True, "username": u.username, "role": u.role})

@app.post("/auth/login")
def login():
    data = request.get_json(force=True)
    u = User.query.filter_by(username=(data.get("username") or "").strip()).first()
    if not u or not check_password_hash(u.pw_hash, (data.get("password") or "")):
        return jsonify({"error": "invalid credentials"}), 401
    # Return role & instruct client to send X-User on calls
    return jsonify({"ok": True, "username": u.username, "role": u.role})

# ---------- Videos ----------
def video_dict(v: Video):
    # average rating
    ratings = Rating.query.filter_by(video_id=v.id).all()
    avg = round(sum(r.value for r in ratings)/len(ratings), 1) if ratings else None
    return {
        "id": v.id, "title": v.title, "description": v.description,
        "publisher": v.publisher, "producer": v.producer,
        "genre": v.genre, "age": v.age,
        "kind": v.kind, "youtube_id": v.youtube_id, "file_url": v.file_url,
        "views": v.views, "likes": v.likes, "rating": avg,
        "created_at": v.created_at.isoformat(),
        "comments": [
            {"id": c.id, "user": c.user, "text": c.text, "created_at": c.created_at.isoformat()}
            for c in Comment.query.filter_by(video_id=v.id).order_by(Comment.created_at.asc()).all()
        ],
    }

@app.get("/api/videos")
def list_videos():
    q = (request.args.get("q") or "").lower()
    genre = (request.args.get("genre") or "").lower()
    sort = request.args.get("sort") or "latest"  # latest|likes|views
    query = Video.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(Video.title.ilike(like), Video.genre.ilike(like), Video.publisher.ilike(like)))
    if genre:
        query = query.filter(Video.genre.ilike(genre))
    if sort == "likes":
        query = query.order_by(Video.likes.desc(), Video.created_at.desc())
    elif sort == "views":
        query = query.order_by(Video.views.desc(), Video.created_at.desc())
    else:
        query = query.order_by(Video.created_at.desc())
    return jsonify([video_dict(v) for v in query.all()])

@app.post("/api/videos/youtube")
@require_role("creator")
def add_youtube():
    """
    JSON body:
    { "youtube_url": "...", "title": "...", "description": "...",
      "publisher": "...", "producer": "...", "genre": "...", "age": "PG" }
    """
    data = request.get_json(force=True)
    url = (data.get("youtube_url") or "").strip()
    yid = parse_youtube_id(url)
    if not yid:
        return jsonify({"error": "Invalid YouTube URL"}), 400
    v = Video(
        title=data.get("title") or "Untitled",
        description=data.get("description"),
        publisher=data.get("publisher"),
        producer=data.get("producer"),
        genre=data.get("genre"),
        age=data.get("age") or "PG",
        kind="youtube",
        youtube_id=yid,
        uploader_id=getattr(request, "_user").id
    )
    db.session.add(v); db.session.commit()
    return jsonify(video_dict(v)), 201

@app.post("/api/videos/<int:vid>/like")
def like_video(vid):
    v = Video.query.get_or_404(vid)
    v.likes += 1
    db.session.commit()
    return jsonify({"likes": v.likes})

@app.post("/api/videos/<int:vid>/comments")
def add_comment(vid):
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    user = (data.get("user") or "guest").strip()
    if not text:
        return jsonify({"error": "text required"}), 400
    v = Video.query.get_or_404(vid)
    c = Comment(video_id=v.id, user=user, text=text)
    db.session.add(c); db.session.commit()
    return jsonify(video_dict(v))

@app.post("/api/videos/<int:vid>/ratings")
def add_rating(vid):
    data = request.get_json(force=True)
    user = (data.get("user") or "guest").strip()
    try:
        value = int(data.get("value"))
    except Exception:
        return jsonify({"error": "value 1..5 required"}), 400
    if value < 1 or value > 5:
        return jsonify({"error": "value 1..5 required"}), 400
    v = Video.query.get_or_404(vid)
    # one per user: update if exists
    r = Rating.query.filter_by(video_id=v.id, user=user).first()
    if r: r.value = value
    else: db.session.add(Rating(video_id=v.id, user=user, value=value))
    db.session.commit()
    return jsonify(video_dict(v))

# ---------- Static / uploads ----------
@app.get("/")
def root():  # serve your existing HTML
    return send_file(os.path.join(BASE_DIR, "index.html"))

UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

@app.get("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# ---------- Utils ----------
def parse_youtube_id(url: str) -> str | None:
    """
    Accept common YouTube URL forms and return the video ID.
    Examples:
      https://www.youtube.com/watch?v=YEyWIyPfQWA
      https://youtu.be/YEyWIyPfQWA
      https://www.youtube.com/embed/YEyWIyPfQWA
    """
    if not url: return None
    u = url.strip()
    for sep in ("v=", "youtu.be/", "/embed/"):
        if sep in u:
            yid = u.split(sep, 1)[1].split("&", 1)[0].split("?", 1)[0].split("/", 1)[0]
            return yid[:32]
    if len(u) in (11, 12):  # if they paste just the id
        return u
    return None

# ---------- Main ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
