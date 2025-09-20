import os
import sqlite3
from flask import Flask, request, render_template_string, send_file, abort, jsonify, Response
from io import BytesIO
import math
import urllib.parse
import requests
import base64, zlib, os

# Paths
DB_FILE = "levels.db"
SAVE_DIR = "./save"
MUSIC_LIB_URL = "https://geometrydashfiles.b-cdn.net/music/musiclibrary_02.dat"
MUSIC_LIB_FILE = "musiclibrary.dat"

# --- GMD Conversion Logic ---
k_tag_map = [
    ("kCEK", "static", 4),
    ("k1", "1"),
    ("k23", "15"),
    ("k2", "2"),
    ("k4", "4"),
    ("k3", "3"),
    ("k21", "static", 3),
    ("k16", "5"),
    ("k17", "13"),
    ("k80", "46"),
    ("k81", "47"),
    ("k64", "37"),
    ("k42", "30"),
    ("k45", "35"),
    ("k50", "static", 45),
    ("k48", "45"),
]

def parse_level_data(data):
    pairs = {}
    parts = data.strip().split(":")
    i = 0
    while i < len(parts) - 1:
        key = parts[i]
        value = parts[i + 1].split(";")[0] if ";" in parts[i + 1] else parts[i + 1]
        pairs[key] = value
        i += 2
    return pairs

def make_gmd(level_id, pairs):
    xml = ['<?xml version="1.0"?><plist version="1.0" gjver="2.0"><dict>']
    for ktag, rawkey, *staticval in k_tag_map:
        if rawkey == "static":
            v = staticval[0]
        else:
            v = pairs.get(rawkey)
        if v is None or v == "":
            continue
        tagtype = "s" if ktag in ("k2", "k4", "k3") else "i"
        xml.append(f'<k>{ktag}</k><{tagtype}>{v}</{tagtype}>')
    xml.append('</dict></plist>')
    return ''.join(xml)

def find_level_file(level_id):
    """Find a file like '{ID} - name.txt' in SAVE_DIR recursively."""
    for root, _, files in os.walk(SAVE_DIR):
        for f in files:
            if f.startswith(f"{level_id} - ") and f.endswith(".txt"):
                return os.path.join(root, f)
    return None

def format_size(size_str):
    """Convert '11601 B' to readable B/KB/MB."""
    if not size_str:
        return ""
    try:
        num = int(size_str.split()[0])
        if num >= 1024**2:
            return f"{num / (1024**2):.2f} MB"
        elif num >= 1024:
            return f"{num / 1024:.2f} KB"
        else:
            return f"{num} B"
    except Exception:
        return size_str

def parse_size_to_int(size_str):
    """Convert size like '11601 B' to integer bytes for filtering."""
    try:
        return int(size_str.split()[0])
    except:
        return 0

def download_musiclibrary(file_name=MUSIC_LIB_FILE):
    if not os.path.exists(file_name):
        r = requests.get(MUSIC_LIB_URL)
        r.raise_for_status()
        with open(file_name, "wb") as f:
            f.write(r.content)

def decode_and_inflate(file_name):
    with open(file_name, "rb") as f:
        encoded = f.read()
    decoded = base64.urlsafe_b64decode(encoded)
    inflated = zlib.decompress(decoded)
    return inflated.decode("utf-8")

def parse_music_library(content):
    version, artists_str, songs_str, tags_str = content.split("|", 3)
    songs = {}
    for entry in songs_str.split(";"):
        if not entry.strip():
            continue
        parts = entry.split(",")
        try:
            song_id = int(parts[0])
        except ValueError:
            continue
        song_name = parts[1] if len(parts) > 1 else f"song_{song_id}"
        songs[song_id] = song_name
    return songs

download_musiclibrary()
_music_content = decode_and_inflate(MUSIC_LIB_FILE)
MUSIC_LIBRARY = parse_music_library(_music_content)

# --- Flask App ---
app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
  <title>Level Browser</title>
  <style>
    body { font-family: sans-serif; margin: 2em; background: #f8f9fa; }
    .search-bar { margin-bottom: 2em; }
    input, select, button { padding: 0.5em; margin: 0.3em; border: 1px solid #ccc; border-radius: 6px; }
    button { background: #007bff; color: white; border: none; cursor: pointer; }
    button:hover { background: #0056b3; }
    .results { display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 1em; }
    .card { background: white; border-radius: 10px; box-shadow: 0 2px 6px rgba(0,0,0,0.1); padding: 1em; position: relative; }
    .card h3 { margin: 0; font-size: 1.2em; }
    .card p { margin: 0.3em 0; }
    .download-btn { display: inline-block; margin-top: 0.5em; padding: 0.4em 0.8em; background: #28a745; color: white; text-decoration: none; border-radius: 5px; }
    .download-btn:hover { background: #1e7e34; }
    .pagination { margin-top: 1em; }
    .pagination form { display: inline; }
    .pagination input[type="number"] { width: 50px; }

    /* (i) info button + panel */
    .info-btn { position: absolute; top: 6px; right: 8px; background: none; border: none; cursor: pointer; font-weight: bold; color: #007bff; }
    .extra-info { display: none; margin-top: 0.6em; font-size: 0.9em; background: #f1f3f5; padding: 0.6em; border-radius: 6px; }
    .extra-info p { margin: 0.2em 0; }
  </style>
  <script>
    function toggleInfo(id) {
      var e = document.getElementById("info-" + id);
      if (e.style.display === "none" || e.style.display === "") {
        e.style.display = "block";
      } else {
        e.style.display = "none";
      }
    }
  </script>
</head>
<body>
  <h1>Level Browser</h1>
  <form class="search-bar" method="get">
    <input type="text" name="level_id" placeholder="Level ID" value="{{level_id}}">
    <input type="text" name="name" placeholder="Level Name" value="{{name}}">
    <input type="text" name="username" placeholder="Username" value="{{username}}">
    <input type="text" name="description" placeholder="Description" value="{{description}}">
    <input type="text" name="song_id" placeholder="Song IDs (comma-separated)" value="{{song_id}}">

    <!-- New exclusive-search fields -->
    <input type="text" name="original_id" placeholder="OriginalID" value="{{original_id}}">
    <input type="text" name="version" placeholder="Version" value="{{version}}">
    <input type="text" name="length" placeholder="Length (Tiny/Short/...)" value="{{length}}">
    <input type="number" name="rcoins" placeholder="rCoins" value="{{rcoins}}">
    <input type="number" name="scoins" placeholder="sCoins" value="{{scoins}}">
    <input type="number" name="min_editor_time" placeholder="Min EditorTime" value="{{min_editor_time}}">
    <input type="number" name="max_editor_time" placeholder="Max EditorTime" value="{{max_editor_time}}">
    <input type="number" name="editor_ctime" placeholder="EditorCTime" value="{{editor_ctime}}">
    <input type="text" name="requested_rating" placeholder="RequestedRating" value="{{requested_rating}}">
    <select name="two_player">
      <option value="" {% if two_player == "" %}selected{% endif %}>TwoPlayer (Any)</option>
      <option value="Yes" {% if two_player == "Yes" %}selected{% endif %}>Yes</option>
      <option value="No" {% if two_player == "No" %}selected{% endif %}>No</option>
    </select>
    <input type="number" name="min_object_count" placeholder="Min ObjectCount" value="{{min_object_count}}">
    <input type="number" name="max_object_count" placeholder="Max ObjectCount" value="{{max_object_count}}">

    <input type="number" name="min_cp" placeholder="Min CP" value="{{min_cp}}">
    <input type="number" name="max_cp" placeholder="Max CP" value="{{max_cp}}">
    <input type="number" name="min_size" placeholder="Min Size (B)" value="{{min_size}}">
    <input type="number" name="max_size" placeholder="Max Size (B)" value="{{max_size}}">
    <select name="sort_by">
      <option value="ID" {% if sort_by == "ID" %}selected{% endif %}>Sort by ID</option>
      <option value="CreatorPoints" {% if sort_by == "CreatorPoints" %}selected{% endif %}>Sort by Creator Points</option>
      <option value="Size" {% if sort_by == "Size" %}selected{% endif %}>Sort by Size</option>
    </select>
    <select name="sort_order">
      <option value="asc" {% if sort_order == "asc" %}selected{% endif %}>Ascending</option>
      <option value="desc" {% if sort_order == "desc" %}selected{% endif %}>Descending</option>
    </select>
    <select name="search_mode">
      <option value="exclusive" {% if search_mode == "exclusive" %}selected{% endif %}>Exclusive</option>
      <option value="contains" {% if search_mode == "contains" %}selected{% endif %}>Contains</option>
    </select>
    <select name="case_sensitive">
      <option value="insensitive" {% if case_sensitive == "insensitive" %}selected{% endif %}>Case Insensitive</option>
      <option value="sensitive" {% if case_sensitive == "sensitive" %}selected{% endif %}>Case Sensitive</option>
    </select>
    <input type="number" name="page_size" min="1" value="{{page_size}}" placeholder="Page size">
    <input type="hidden" name="page" value="{{page}}">
    <button type="submit">Search</button>
  </form>

  {% if results %}
  <div class="results">
    {% for row in results %}
    <div class="card">
      <button class="info-btn" onclick="toggleInfo({{row[0]}})">(i)</button>
      <h3>{{row[1]}}</h3>
      <p><b>ID:</b> {{row[0]}}</p>
      <p><b>Creator:</b> {{row[2]}}</p>
      <p><b>CP:</b> {{row[3]}}</p>
      <p><b>Description:</b> {{row[4]}}</p>
      <p><b>Song IDs:</b> 
        {% for song_id in row[6].split(",") %}
          <a href="/downloadSong/{{ song_id|trim }}">{{ song_id|trim }}</a>{% if not loop.last %}, {% endif %}
        {% endfor %}
      </p>
      <p><b>Size:</b> {{row[5]}}</p>
      <a class="download-btn" href="/download/{{row[0]}}">Download GMD</a>

      <div class="extra-info" id="info-{{row[0]}}">
        <p><b>OriginalID:</b> {{row[7]}}</p>
        <p><b>rCoins:</b> {{row[8]}}</p>
        <p><b>sCoins:</b> {{row[9]}}</p>
        <p><b>Version:</b> {{row[10]}}</p>
        <p><b>Length:</b> {{row[11]}}</p>
        <p><b>EditorTime:</b> {{row[12]}}</p>
        <p><b>EditorCTime:</b> {{row[13]}}</p>
        <p><b>RequestedRating:</b> {{row[14]}}</p>
        <p><b>TwoPlayer:</b> {{row[15]}}</p>
        <p><b>ObjectCount:</b> {{row[16]}}</p>
      </div>
    </div>
    {% endfor %}
  </div>

  <div class="pagination">
    <form method="get" style="display:inline;">
      {% for key, value in request.args.items() %}
        {% if key != "page" %}
          <input type="hidden" name="{{key}}" value="{{value}}">
        {% endif %}
      {% endfor %}
      <button type="submit" name="page" value="{{page-1}}" {% if page <= 1 %}disabled{% endif %}>Previous</button>
    </form>

    Page <form method="get" style="display:inline;">
      <input type="number" name="page" value="{{page}}" min="1" max="{{total_pages}}">
      {% for key, value in request.args.items() %}
        {% if key != "page" %}
          <input type="hidden" name="{{key}}" value="{{value}}">
        {% endif %}
      {% endfor %}
      <button type="submit">Go</button>
    </form>

    <form method="get" style="display:inline;">
      {% for key, value in request.args.items() %}
        {% if key != "page" %}
          <input type="hidden" name="{{key}}" value="{{value}}">
        {% endif %}
      {% endfor %}
      <button type="submit" name="page" value="{{page+1}}" {% if page >= total_pages %}disabled{% endif %}>Next</button>
    </form>

    <p>Page {{page}} of {{total_pages}}</p>
  </div>

  {% elif searched %}
    <p>No results found.</p>
  {% endif %}
</body>
</html>
"""

def search_levels(level_id, name, username, description, song_id, min_cp, max_cp,
                  min_size, max_size, search_mode, case_sensitive,
                  sort_by, sort_order, page, page_size,
                  original_id, rcoins, scoins, version, length,
                  min_editor_time, max_editor_time, editor_ctime,
                  requested_rating, two_player, min_object_count, max_object_count):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    select_sql = """
        SELECT
          ID, Name, Username, CreatorPoints, Description, Size, songID,
          OriginalID, rCoins, sCoins, Version, Length, EditorTime, EditorCTime,
          RequestedRating, TwoPlayer, ObjectCount
        FROM levels
        WHERE 1=1
    """

    where = []
    params = []

    # Helper functions
    def exact_text(field, value):
        if value is None or value == "":
            return
        if case_sensitive == "sensitive":
            where.append(f"{field} = ?")
            params.append(value)
        else:
            where.append(f"LOWER({field}) = LOWER(?)")
            params.append(value)

    def contains_text(field, value):
        if value is None or value == "":
            return
        if case_sensitive == "sensitive":
            where.append(f"{field} LIKE ?")
            params.append(f"%{value}%")
        else:
            where.append(f"LOWER({field}) LIKE LOWER(?)")
            params.append(f"%{value}%")

    def text_filter(field, value, exclusive=None):
        # If exclusive not specified, use global search_mode
        if exclusive is None:
            exclusive = (search_mode == "exclusive")
        if exclusive:
            exact_text(field, value)
        else:
            contains_text(field, value)

    def exact_num(field, value):
        if value is None or value == "":
            return
        where.append(f"{field} != '' AND {field} = ?")
        params.append(value)

    def range_min(field, value):
        if value is None or value == "":
            return
        where.append(f"{field} != '' AND {field} >= ?")
        params.append(value)

    def range_max(field, value):
        if value is None or value == "":
            return
        where.append(f"{field} != '' AND {field} <= ?")
        params.append(value)

    # Level ID (always exact)
    if level_id:
        if case_sensitive == "sensitive":
            where.append("CAST(ID AS TEXT) = ?")
            params.append(level_id)
        else:
            where.append("LOWER(CAST(ID AS TEXT)) = LOWER(?)")
            params.append(level_id)

    # Name, Username, Description
    text_filter("Name", name)
    text_filter("Username", username)
    text_filter("Description", description)

    # Song IDs
    if song_id:
        song_ids = [s.strip() for s in song_id.split(",") if s.strip()]
        for sid in song_ids:
            where.append("(',' || songID || ',') LIKE ?")
            params.append(f"%,{sid},%")

    # New fields
    text_filter("OriginalID", original_id, exclusive=True)
    exact_num("rCoins", rcoins)
    exact_num("sCoins", scoins)
    text_filter("Version", version, exclusive=True)
    text_filter("Length", length, exclusive=True)
    range_min("EditorTime", min_editor_time)
    range_max("EditorTime", max_editor_time)
    exact_num("EditorCTime", editor_ctime)
    text_filter("RequestedRating", requested_rating, exclusive=True)
    if two_player:
        text_filter("TwoPlayer", two_player, exclusive=True)
    range_min("ObjectCount", min_object_count)
    range_max("ObjectCount", max_object_count)

    # CP range
    range_min("CreatorPoints", min_cp)
    range_max("CreatorPoints", max_cp)

    # Size range
    if min_size:
        where.append("CAST(REPLACE(Size,' B','') AS INTEGER) >= ?")
        params.append(min_size)
    if max_size:
        where.append("CAST(REPLACE(Size,' B','') AS INTEGER) <= ?")
        params.append(max_size)

    # Assemble final SQL
    sql = select_sql + (" AND " + " AND ".join(where) if where else "")

    # Sorting
    if sort_by == "Size":
        sql += f" ORDER BY CAST(REPLACE(Size,' B','') AS INTEGER) {sort_order.upper()}"
    elif sort_by in ("ID", "CreatorPoints"):
        sql += f" ORDER BY {sort_by} {sort_order.upper()}"

    # Pagination
    offset = (page - 1) * page_size
    sql_with_pagination = sql + " LIMIT ? OFFSET ?"
    params_with_pagination = params + [page_size, offset]

    cur.execute(sql_with_pagination, params_with_pagination)
    results = cur.fetchall()

    # Total count
    count_sql = "SELECT COUNT(*) FROM levels WHERE 1=1" + (" AND " + " AND ".join(where) if where else "")
    cur.execute(count_sql, params)
    total_count = cur.fetchone()[0]

    conn.close()

    results = [(
        row[0], row[1], row[2], row[3], row[4],
        format_size(row[5]),  # Size formatted
        row[6],  # songID
        row[7],  # OriginalID
        row[8],  # rCoins
        row[9],  # sCoins
        row[10], # Version
        row[11], # Length
        row[12], # EditorTime
        row[13], # EditorCTime
        row[14], # RequestedRating
        row[15], # TwoPlayer
        row[16], # ObjectCount
    ) for row in results]

    return results, total_count

@app.route("/")
def index():
    level_id = request.args.get("level_id", "")
    name = request.args.get("name", "")
    username = request.args.get("username", "")
    description = request.args.get("description", "")
    song_id = request.args.get("song_id", "")

    # New fields
    original_id = request.args.get("original_id", "")
    rcoins = request.args.get("rcoins", "")
    scoins = request.args.get("scoins", "")
    version = request.args.get("version", "")
    length = request.args.get("length", "")
    min_editor_time = request.args.get("min_editor_time", "")
    max_editor_time = request.args.get("max_editor_time", "")
    editor_ctime = request.args.get("editor_ctime", "")
    requested_rating = request.args.get("requested_rating", "")
    two_player = request.args.get("two_player", "")
    min_object_count = request.args.get("min_object_count", "")
    max_object_count = request.args.get("max_object_count", "")

    min_cp = request.args.get("min_cp")
    max_cp = request.args.get("max_cp")
    min_size = request.args.get("min_size")
    max_size = request.args.get("max_size")
    search_mode = request.args.get("search_mode", "contains")
    case_sensitive = request.args.get("case_sensitive", "insensitive")
    sort_by = request.args.get("sort_by", "ID")
    sort_order = request.args.get("sort_order", "desc")
    page_size = int(request.args.get("page_size", 10))
    page = int(request.args.get("page", 1))

    results, total_count = search_levels(
        level_id, name, username, description, song_id,
        min_cp, max_cp, min_size, max_size,
        search_mode, case_sensitive, sort_by,
        sort_order, page, page_size,
        original_id, rcoins, scoins, version, length,
        min_editor_time, max_editor_time, editor_ctime,
        requested_rating, two_player, min_object_count, max_object_count
    )

    total_pages = max(1, math.ceil(total_count / page_size))

    return render_template_string(
        HTML,
        level_id=level_id, name=name, username=username,
        description=description, song_id=song_id,
        min_cp=min_cp, max_cp=max_cp,
        min_size=min_size, max_size=max_size,
        search_mode=search_mode, case_sensitive=case_sensitive,
        sort_by=sort_by, sort_order=sort_order,
        results=results, searched=True,
        page=page, page_size=page_size, total_pages=total_pages,
        request=request,
        original_id=original_id, rcoins=rcoins, scoins=scoins,
        version=version, length=length,
        min_editor_time=min_editor_time, max_editor_time=max_editor_time,
        editor_ctime=editor_ctime, requested_rating=requested_rating,
        two_player=two_player, min_object_count=min_object_count,
        max_object_count=max_object_count
    )

@app.route("/download/<int:level_id>")
def download(level_id):
    file_path = find_level_file(str(level_id))
    if not file_path:
        abort(404, description="Level file not found")

    filename = os.path.splitext(os.path.basename(file_path))[0]  # remove extension
    if " - " in filename:
        _, level_name = filename.split(" - ", 1)
    else:
        level_name = filename

    safe_level_name = "".join(c for c in level_name if c.isalnum() or c in " _-").rstrip()

    with open(file_path, "r", encoding="utf-8") as f:
        data = f.read()
    pairs = parse_level_data(data)
    xml_content = make_gmd(level_id, pairs)

    buf = BytesIO(xml_content.encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f"{level_id} - {safe_level_name}.gmd",
        mimetype="application/octet-stream"
    )

@app.route("/downloadSong/<int:songID>")
def getSongURL(songID):
    try:
        if songID >= 10000000:
            # Direct CDN OGG file
            songURL = f"https://geometrydashfiles.b-cdn.net/music/{songID}.ogg"
            songName = MUSIC_LIBRARY.get(songID, f"song_{songID}")
            mimetype = "audio/ogg"
            ext = "ogg"
        else:
            # Use Boomlings API
            url = "http://www.boomlings.com/database/getGJSongInfo.php"
            data = {
                "secret": "Wmfd2893gb7",
                "binaryVersion": 45,
                "songID": songID
            }
            headers = {"User-Agent": ""}
            response = requests.post(url, data=data, headers=headers)
            response.raise_for_status()

            level = response.text
            parts = level.split("~|~")
            parsed = {parts[i]: parts[i + 1] for i in range(0, len(parts) - 1, 2)}

            songURL = urllib.parse.unquote(parsed.get("10"))
            songName = parsed.get("2", f"song_{songID}")
            mimetype = "audio/mpeg"
            ext = "mp3"

        # Download file into memory
        r = requests.get(songURL, stream=True)
        r.raise_for_status()
        file_data = BytesIO(r.content)

        # Send back to client
        return send_file(
            file_data,
            as_attachment=True,
            download_name=f"{songName}.{ext}",
            mimetype=mimetype
        )

    except Exception as e:
        return Response(f"Song is not available, this can happen if it's a main level song, or if it was deleted.", status=500)
    
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)