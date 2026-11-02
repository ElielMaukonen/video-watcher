#!/usr/bin/env python3
"""Post videos to TikTok through the official Content Posting API.

Free: it talks straight to TikTok with your own developer app, so there is no
scheduler subscription and no per-month post cap beyond TikTok's own limits.
Python stdlib only; ffprobe is used for a duration check when it is present.

    py tiktok_post.py --check                 what is configured, what is missing
    py tiktok_post.py setup                   store client key/secret (prompts)
    py tiktok_post.py auth                    log in (browser + localhost callback)
    py tiktok_post.py auth-url                two-step login, step 1: print the URL
    py tiktok_post.py auth-finish "<URL>"     step 2: paste the URL the browser landed on
    py tiktok_post.py whoami                  creator info: name, privacy options, max length
    py tiktok_post.py post VIDEO --title "..." --privacy SELF_ONLY
    py tiktok_post.py post VIDEO --draft      send to the TikTok app inbox instead
    py tiktok_post.py batch posts.json        many posts, paced, resumable
    py tiktok_post.py status PUBLISH_ID

Everything it stores lives in `.tiktok/` at the repo root (gitignored), or in
$TIKTOK_HOME if set. Credentials come from the environment first:
TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT_URI.

Exit codes: 0 ok, 1 error, 2 usage/config, 3 TikTok's daily post cap reached.
"""
import argparse
import base64
import csv
import getpass
import hashlib
import http.server
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
API = 'https://open.tiktokapis.com'
AUTHORIZE_URL = 'https://www.tiktok.com/v2/auth/authorize/'
SCOPES = 'user.info.basic,video.publish,video.upload'
DEFAULT_REDIRECT = 'http://localhost:3455/callback/'

PRIVACY_LEVELS = ('PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS',
                  'FOLLOWER_OF_CREATOR', 'SELF_ONLY')
CONTENT_TYPES = {'.mp4': 'video/mp4', '.mov': 'video/quicktime',
                 '.webm': 'video/webm'}

MB = 1024 * 1024
MIN_CHUNK = 5 * MB          # smaller files go up whole
MAX_CHUNK = 64 * MB         # largest single chunk TikTok accepts
CHUNK = 10 * MB             # chunk size for files over MAX_CHUNK
MAX_FILE = 4 * 1024 * MB
TITLE_MAX = 2200            # UTF-16 code units

INIT_SPACING_S = 11         # init endpoints allow 6 requests/min per token
STATUS_POLL_S = 5           # status endpoint allows 30 requests/min
STATUS_TIMEOUT_S = 600
REFRESH_MARGIN_S = 300

EXIT_CAP = 3
CAP_CODES = {'spam_risk_too_many_posts', 'spam_risk_too_many_pending_share',
             'reached_active_user_cap'}
FATAL_CODES = {'spam_risk_user_banned_from_posting', 'access_token_invalid',
               'scope_not_authorized'}


class TikTokError(Exception):
    def __init__(self, code, message='', log_id='', http_status=None):
        self.code = code or 'unknown_error'
        self.message = message or ''
        self.log_id = log_id or ''
        self.http_status = http_status
        super().__init__(self.__str__())

    def __str__(self):
        s = self.code
        if self.message:
            s += ': ' + self.message
        if self.http_status:
            s += f' (HTTP {self.http_status})'
        if self.log_id:
            s += f' [log_id {self.log_id}]'
        return s


class ConfigError(Exception):
    pass


# ------------------------------------------------------------------ paths ----
def home():
    env = os.environ.get('TIKTOK_HOME')
    return Path(env).expanduser() if env else REPO / '.tiktok'


def _write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None


def check_gitignore():
    """The token dir must never be committed. Warn loudly if it could be."""
    if os.environ.get('TIKTOK_HOME'):
        return True
    gi = REPO / '.gitignore'
    try:
        lines = [l.strip() for l in gi.read_text(encoding='utf-8').splitlines()]
    except FileNotFoundError:
        lines = []
    if any(l in ('/.tiktok/', '.tiktok/', '/.tiktok', '.tiktok') for l in lines):
        return True
    print('WARNING: .tiktok/ is not in .gitignore. It holds your TikTok tokens '
          'and app secret - add "/.tiktok/" to .gitignore before committing.',
          file=sys.stderr)
    return False


# ----------------------------------------------------------------- config ----
def load_config():
    cfg = _read_json(home() / 'config.json') or {}
    key = os.environ.get('TIKTOK_CLIENT_KEY') or cfg.get('client_key')
    secret = os.environ.get('TIKTOK_CLIENT_SECRET') or cfg.get('client_secret')
    redirect = (os.environ.get('TIKTOK_REDIRECT_URI') or cfg.get('redirect_uri')
                or DEFAULT_REDIRECT)
    return {'client_key': key, 'client_secret': secret, 'redirect_uri': redirect}


def require_config():
    cfg = load_config()
    missing = [k for k in ('client_key', 'client_secret') if not cfg[k]]
    if missing:
        raise ConfigError(
            'missing ' + ', '.join(missing) + '. Run "setup", or set '
            'TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET.')
    return cfg


# ------------------------------------------------------------------- http ----
def _request(url, data=None, headers=None, method=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _parse(status, body):
    try:
        return json.loads(body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        raise TikTokError('bad_response', body[:300].decode('utf-8', 'replace'),
                          http_status=status)


def api_post(path, token, payload):
    status, body = _request(
        API + path, data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + token,
                 'Content-Type': 'application/json; charset=UTF-8'},
        method='POST')
    doc = _parse(status, body)
    err = doc.get('error') or {}
    code = err.get('code')
    if status >= 400 or (code and code != 'ok'):
        raise TikTokError(code or f'http_{status}', err.get('message'),
                          err.get('log_id') or err.get('logid'), status)
    return doc.get('data') or {}


def oauth_post(params):
    status, body = _request(
        API + '/v2/oauth/token/',
        data=urllib.parse.urlencode(params).encode('ascii'),
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Cache-Control': 'no-cache'},
        method='POST')
    doc = _parse(status, body)
    if status >= 400 or doc.get('error') or not doc.get('access_token'):
        err = doc.get('error')
        if isinstance(err, dict):
            raise TikTokError(err.get('code'), err.get('message'),
                              err.get('log_id'), status)
        raise TikTokError(err or f'http_{status}', doc.get('error_description'),
                          doc.get('log_id'), status)
    return doc


# ------------------------------------------------------------------ oauth ----
def pkce_pair():
    """TikTok's desktop Login Kit wants the challenge as HEX SHA-256, not the
    base64url of RFC 7636. Its docs say so explicitly."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = hashlib.sha256(verifier.encode('ascii')).hexdigest()
    return verifier, challenge


def build_auth_url(cfg, state, challenge):
    q = {'client_key': cfg['client_key'], 'response_type': 'code',
         'scope': SCOPES, 'redirect_uri': cfg['redirect_uri'], 'state': state,
         'code_challenge': challenge, 'code_challenge_method': 'S256'}
    return AUTHORIZE_URL + '?' + urllib.parse.urlencode(q)


def parse_callback(text):
    """Accept the full URL the browser landed on, or just its query string.
    Returns (code, state). Raises TikTokError if TikTok returned an error."""
    text = text.strip().strip('"\'')
    query = urllib.parse.urlparse(text).query if '://' in text else text.lstrip('?')
    q = urllib.parse.parse_qs(query)
    if 'error' in q:
        raise TikTokError(q['error'][0], (q.get('error_description') or [''])[0])
    if 'code' not in q:
        raise ConfigError('no "code" in that URL. Paste the whole address the '
                          'browser shows after you approve the app.')
    return q['code'][0], (q.get('state') or [None])[0]


def _token_record(doc, now=None):
    now = now or time.time()
    return {
        'access_token': doc['access_token'],
        'expires_at': now + int(doc.get('expires_in') or 0),
        'refresh_token': doc.get('refresh_token'),
        'refresh_expires_at': now + int(doc.get('refresh_expires_in') or 0),
        'open_id': doc.get('open_id'),
        'scope': doc.get('scope'),
    }


def start_auth():
    cfg = require_config()
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    _write_private(home() / 'pending_auth.json',
                   {'state': state, 'verifier': verifier,
                    'redirect_uri': cfg['redirect_uri'], 'created': time.time()})
    return cfg, state, build_auth_url(cfg, state, challenge)


def finish_auth(callback_text):
    cfg = require_config()
    pending = _read_json(home() / 'pending_auth.json')
    if not pending:
        raise ConfigError('no login in progress. Run "auth-url" first.')
    code, state = parse_callback(callback_text)
    if state != pending['state']:
        raise ConfigError('state mismatch - that URL belongs to a different '
                          'login attempt. Run "auth-url" again.')
    doc = oauth_post({
        'client_key': cfg['client_key'], 'client_secret': cfg['client_secret'],
        'code': code, 'grant_type': 'authorization_code',
        'redirect_uri': pending['redirect_uri'],
        'code_verifier': pending['verifier']})
    rec = _token_record(doc)
    _write_private(home() / 'token.json', rec)
    (home() / 'pending_auth.json').unlink(missing_ok=True)
    granted = set((rec.get('scope') or '').split(','))
    missing = {'video.publish', 'video.upload'} - granted
    if missing:
        print('WARNING: login succeeded but these scopes were not granted: '
              + ', '.join(sorted(missing)) + '. Add the Content Posting API '
              'product to your app and enable them.', file=sys.stderr)
    return rec


def get_access_token():
    tok = _read_json(home() / 'token.json')
    if not tok:
        raise ConfigError('not logged in. Run "auth" (or "auth-url" then '
                          '"auth-finish").')
    if tok['expires_at'] - time.time() > REFRESH_MARGIN_S:
        return tok['access_token']
    if not tok.get('refresh_token') or tok.get('refresh_expires_at', 0) < time.time():
        raise ConfigError('login expired. Run "auth" again.')
    cfg = require_config()
    doc = oauth_post({
        'client_key': cfg['client_key'], 'client_secret': cfg['client_secret'],
        'grant_type': 'refresh_token', 'refresh_token': tok['refresh_token']})
    # Refresh tokens rotate: the old one is dead the moment this returns.
    rec = _token_record(doc)
    rec['open_id'] = rec['open_id'] or tok.get('open_id')
    rec['scope'] = rec['scope'] or tok.get('scope')
    _write_private(home() / 'token.json', rec)
    return rec['access_token']


def interactive_auth(timeout=300):
    cfg, state, url = start_auth()
    print('Open this URL, log in to TikTok and approve the app:\n')
    print(url + '\n')
    redirect = urllib.parse.urlparse(cfg['redirect_uri'])
    captured = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if urllib.parse.urlparse(self.path).path.rstrip('/') != \
                    redirect.path.rstrip('/'):
                self.send_response(404)
                self.end_headers()
                return
            captured['url'] = 'http://localhost' + self.path
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write('<h2>Done - you can close this tab.</h2>'.encode())

        def log_message(self, *a):
            pass

    server = None
    if redirect.hostname in ('localhost', '127.0.0.1') and redirect.port:
        try:
            server = http.server.HTTPServer(('127.0.0.1', redirect.port), Handler)
        except OSError as e:
            print(f'(could not listen on port {redirect.port}: {e})')
    if server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f'Waiting up to {timeout // 60} min for the browser to come back '
              f'to {cfg["redirect_uri"]} ...')
        print('If the browser shows "can\'t connect" instead, copy the address '
              'it shows and run:  auth-finish "<that address>"')
        deadline = time.time() + timeout
        while 'url' not in captured and time.time() < deadline:
            time.sleep(0.5)
        server.shutdown()
        if 'url' not in captured:
            raise ConfigError('timed out waiting for the browser. The login is '
                              'still pending - use auth-finish with the address '
                              'the browser landed on.')
        return finish_auth(captured['url'])
    pasted = input('Paste the full address the browser landed on: ')
    return finish_auth(pasted)


# ------------------------------------------------------------------ video ----
def chunk_plan(size):
    """(chunk_size, total_chunk_count) per TikTok's media transfer rules:
    under 5 MB or up to 64 MB -> one chunk; bigger -> 10 MB chunks with the
    remainder folded into the last one (count = floor(size / chunk))."""
    if size <= 0:
        raise ValueError('empty file')
    if size > MAX_FILE:
        raise ValueError('file is over TikTok\'s 4 GB limit')
    if size <= MAX_CHUNK:
        return size, 1
    return CHUNK, size // CHUNK


def chunk_ranges(size, chunk_size, count):
    """Inclusive byte ranges for each PUT, last chunk absorbing the remainder."""
    out = []
    for i in range(count):
        start = i * chunk_size
        end = size - 1 if i == count - 1 else start + chunk_size - 1
        out.append((start, end))
    return out


def probe_duration(path):
    if not shutil.which('ffprobe'):
        return None
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=nw=1:nk=1', str(path)],
            capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def utf16_len(s):
    return len(s.encode('utf-16-le')) // 2


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(MB), b''):
            h.update(block)
    return h.hexdigest()


def upload_file(upload_url, path, size, chunk_size, count, content_type):
    ranges = chunk_ranges(size, chunk_size, count)
    with open(path, 'rb') as f:
        for i, (start, end) in enumerate(ranges, 1):
            f.seek(start)
            data = f.read(end - start + 1)
            for attempt in range(3):
                status, body = _request(
                    upload_url, data=data, method='PUT', timeout=600,
                    headers={'Content-Type': content_type,
                             'Content-Length': str(len(data)),
                             'Content-Range': f'bytes {start}-{end}/{size}'})
                if status in (200, 201, 206):
                    break
                if status < 500 or attempt == 2:
                    raise TikTokError(
                        'upload_failed',
                        f'chunk {i}/{count}: '
                        + body[:200].decode('utf-8', 'replace'),
                        http_status=status)
                time.sleep(2 ** (attempt + 1))
            if count > 1:
                print(f'  uploaded chunk {i}/{count}')


# ------------------------------------------------------------------- post ----
def creator_info(token):
    return api_post('/v2/post/publish/creator_info/query/', token, {})


def validate_post(spec, info):
    """Check one post against TikTok's rules and this creator's settings
    before spending an init call. Returns a list of problems."""
    problems = []
    title = spec.get('title') or ''
    if utf16_len(title) > TITLE_MAX:
        problems.append(f'title is {utf16_len(title)} characters; max {TITLE_MAX}')
    if spec.get('draft'):
        return problems
    privacy = spec.get('privacy')
    if not privacy:
        problems.append('privacy is required for a direct post (TikTok does '
                        'not allow a default). Options: '
                        + ', '.join(info.get('privacy_level_options') or PRIVACY_LEVELS))
    elif info.get('privacy_level_options') and \
            privacy not in info['privacy_level_options']:
        problems.append(f'privacy {privacy} is not available for this account; '
                        'options: ' + ', '.join(info['privacy_level_options']))
    if spec.get('brand_content') and privacy == 'SELF_ONLY':
        problems.append('branded content (paid partnership) cannot be '
                        'SELF_ONLY - TikTok rejects that combination')
    dur, max_dur = spec.get('_duration'), info.get('max_video_post_duration_sec')
    if dur and max_dur and dur > max_dur + 0.5:
        problems.append(f'video is {dur:.0f}s; this account can post at most '
                        f'{max_dur}s')
    return problems


def init_post(token, spec, info, size=None, chunk_size=None, count=None):
    if spec.get('url'):
        source = {'source': 'PULL_FROM_URL', 'video_url': spec['url']}
    else:
        source = {'source': 'FILE_UPLOAD', 'video_size': size,
                  'chunk_size': chunk_size, 'total_chunk_count': count}
    if spec.get('draft'):
        return api_post('/v2/post/publish/inbox/video/init/', token,
                        {'source_info': source})
    post_info = {
        'title': spec.get('title') or '',
        'privacy_level': spec['privacy'],
        'disable_comment': bool(spec.get('disable_comment')
                                or info.get('comment_disabled')),
        'disable_duet': bool(spec.get('disable_duet') or info.get('duet_disabled')),
        'disable_stitch': bool(spec.get('disable_stitch')
                               or info.get('stitch_disabled')),
        'brand_content_toggle': bool(spec.get('brand_content')),
        'brand_organic_toggle': bool(spec.get('brand_organic')),
        'is_aigc': bool(spec.get('ai')),
    }
    if spec.get('cover_ms') is not None:
        post_info['video_cover_timestamp_ms'] = int(spec['cover_ms'])
    return api_post('/v2/post/publish/video/init/', token,
                    {'post_info': post_info, 'source_info': source})


def fetch_status(token, publish_id):
    return api_post('/v2/post/publish/status/fetch/', token,
                    {'publish_id': publish_id})


def wait_for_status(token, publish_id, timeout=STATUS_TIMEOUT_S):
    deadline = time.time() + timeout
    last = None
    while True:
        st = fetch_status(token, publish_id)
        status = st.get('status')
        if status != last:
            print(f'  status: {status}')
            last = status
        if status in ('PUBLISH_COMPLETE', 'SEND_TO_USER_INBOX', 'FAILED'):
            return st
        if time.time() > deadline:
            return st
        time.sleep(STATUS_POLL_S)


def log_post(record):
    path = home() / 'posts.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def posted_hashes():
    path = home() / 'posts.jsonl'
    out = set()
    if not path.exists():
        return out
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get('sha256') and r.get('result') in ('PUBLISH_COMPLETE',
                                                    'SEND_TO_USER_INBOX',
                                                    'submitted'):
            out.add(r['sha256'])
    return out


def post_one(spec, token=None, info=None, wait=True):
    """Upload and publish one video. Returns the final record."""
    token = token or get_access_token()
    if info is None:
        info = creator_info(token)
    size = chunk_size = count = None
    content_type = None
    if not spec.get('url'):
        path = Path(spec['video']).expanduser()
        if not path.is_file():
            raise ConfigError(f'no such file: {path}')
        content_type = CONTENT_TYPES.get(path.suffix.lower())
        if not content_type:
            raise ConfigError(f'{path.suffix or "no extension"}: TikTok takes '
                              'MP4, MOV or WebM')
        size = path.stat().st_size
        chunk_size, count = chunk_plan(size)
        spec['_duration'] = probe_duration(path)
    problems = validate_post(spec, info)
    if problems:
        raise ConfigError('; '.join(problems))

    data = init_post(token, spec, info, size, chunk_size, count)
    publish_id = data['publish_id']
    print(f'  publish_id: {publish_id}')
    if not spec.get('url'):
        upload_file(data['upload_url'], path, size, chunk_size, count,
                    content_type)
    rec = {'time': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'video': spec.get('video') or spec.get('url'),
           'sha256': file_sha256(path) if not spec.get('url') else None,
           'title': spec.get('title') or '',
           'privacy': None if spec.get('draft') else spec.get('privacy'),
           'mode': 'draft' if spec.get('draft') else 'direct',
           'publish_id': publish_id, 'result': 'submitted'}
    if wait:
        st = wait_for_status(token, publish_id)
        rec['result'] = st.get('status') or 'unknown'
        if st.get('fail_reason'):
            rec['fail_reason'] = st['fail_reason']
        ids = st.get('publicaly_available_post_id') or \
            st.get('publicly_available_post_id')
        if ids:
            rec['post_ids'] = ids
    log_post(rec)
    return rec


# ------------------------------------------------------------------ batch ----
BOOL_FIELDS = ('draft', 'disable_comment', 'disable_duet', 'disable_stitch',
               'brand_content', 'brand_organic', 'ai')


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ('1', 'true', 'yes', 'y', 'x', 'kyllä')


def load_batch(path):
    path = Path(path)
    if path.suffix.lower() == '.csv':
        with open(path, newline='', encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
    else:
        rows = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(rows, dict):
            rows = rows.get('posts') or []
    specs = []
    for i, row in enumerate(rows, 1):
        spec = {k: v for k, v in row.items() if v not in (None, '')}
        if not spec.get('video') and not spec.get('url'):
            raise ConfigError(f'row {i}: needs "video" (a file) or "url"')
        for k in BOOL_FIELDS:
            if k in spec:
                spec[k] = _truthy(spec[k])
        if 'privacy' in spec:
            spec['privacy'] = str(spec['privacy']).strip().upper()
        if 'cover_ms' in spec:
            spec['cover_ms'] = int(spec['cover_ms'])
        if spec.get('video'):
            v = Path(spec['video']).expanduser()
            spec['video'] = str(v if v.is_absolute() else path.parent / v)
        specs.append(spec)
    return specs


def run_batch(specs, defaults, dry_run=False, skip_posted=True, wait=True):
    token = None if dry_run else get_access_token()
    info = creator_info(token) if token else {}
    done = posted_hashes() if skip_posted else set()
    results = {'posted': 0, 'skipped': 0, 'failed': 0, 'remaining': 0}
    if dry_run:
        results = {'valid': 0, 'skipped': 0, 'failed': 0}
    last_init = 0.0
    for i, raw in enumerate(specs, 1):
        spec = {**defaults, **raw}
        label = spec.get('video') or spec.get('url')
        print(f'[{i}/{len(specs)}] {label}')
        if spec.get('video') and skip_posted and Path(spec['video']).is_file() \
                and file_sha256(spec['video']) in done:
            print('  already posted - skipping')
            results['skipped'] += 1
            continue
        if dry_run:
            problems = []
            if spec.get('video'):
                v = Path(spec['video'])
                if not v.is_file():
                    problems.append('file not found')
                elif v.suffix.lower() not in CONTENT_TYPES:
                    problems.append('TikTok takes MP4, MOV or WebM')
                else:
                    spec['_duration'] = probe_duration(v)
            problems += validate_post(spec, info)
            print('  ok' if not problems else '  PROBLEM: ' + '; '.join(problems))
            results['failed' if problems else 'valid'] += 1
            continue
        wait_s = INIT_SPACING_S - (time.time() - last_init)
        if wait_s > 0:
            time.sleep(wait_s)
        last_init = time.time()
        try:
            token = get_access_token()
            rec = post_one(spec, token, info, wait=wait)
            ok = rec['result'] in ('PUBLISH_COMPLETE', 'SEND_TO_USER_INBOX',
                                   'submitted', 'PROCESSING_UPLOAD',
                                   'PROCESSING_DOWNLOAD')
            results['posted' if ok else 'failed'] += 1
            if not ok:
                print(f'  FAILED: {rec.get("fail_reason") or rec["result"]}')
                if rec.get('fail_reason') in CAP_CODES:
                    results['remaining'] = len(specs) - i + 1
                    results['failed'] -= 1
                    print('TikTok\'s daily post limit reached. Re-run the same '
                          'batch tomorrow; finished posts are skipped.')
                    return results, EXIT_CAP
        except TikTokError as e:
            if e.code in CAP_CODES:
                results['remaining'] = len(specs) - i + 1
                print(f'  STOP: {e}')
                print('TikTok\'s daily post limit reached. Re-run the same '
                      'batch tomorrow; finished posts are skipped.')
                return results, EXIT_CAP
            print(f'  FAILED: {e}')
            results['failed'] += 1
            if e.code in FATAL_CODES:
                results['remaining'] = len(specs) - i
                return results, 1
        except ConfigError as e:
            print(f'  FAILED: {e}')
            results['failed'] += 1
    return results, 0 if results['failed'] == 0 else 1


# -------------------------------------------------------------------- cli ----
def cmd_check(_args):
    ok = True
    cfg = load_config()
    print(f'token dir     : {home()}')
    for k in ('client_key', 'client_secret'):
        print(f'{k:<14}: {"set" if cfg[k] else "MISSING"}')
        ok &= bool(cfg[k])
    print(f'redirect_uri  : {cfg["redirect_uri"]}  (must be registered in the app)')
    tok = _read_json(home() / 'token.json')
    if not tok:
        print('login         : not logged in')
        ok = False
    else:
        left = tok.get('refresh_expires_at', 0) - time.time()
        print(f'login         : open_id {tok.get("open_id")}, scopes '
              f'{tok.get("scope")}, valid {max(0, left) / 86400:.0f} more days')
        ok &= left > 0
    print(f'ffprobe       : {"found" if shutil.which("ffprobe") else "missing (duration check skipped)"}')
    check_gitignore()
    print('ready.' if ok else 'not ready.')
    return 0 if ok else 2


def cmd_setup(args):
    cfg = _read_json(home() / 'config.json') or {}
    key = args.client_key or os.environ.get('TIKTOK_CLIENT_KEY') or \
        input('Client key: ').strip()
    secret = os.environ.get('TIKTOK_CLIENT_SECRET') or \
        getpass.getpass('Client secret (hidden): ').strip()
    cfg.update({'client_key': key, 'client_secret': secret})
    if args.redirect_uri:
        cfg['redirect_uri'] = args.redirect_uri
    _write_private(home() / 'config.json', cfg)
    check_gitignore()
    print(f'saved to {home() / "config.json"}')
    return 0


def cmd_auth(args):
    rec = interactive_auth(timeout=args.timeout)
    print(f'logged in. open_id {rec["open_id"]}, scopes {rec["scope"]}')
    return 0


def cmd_auth_url(_args):
    cfg, _state, url = start_auth()
    print(url)
    print(f'\nAfter approving, the browser goes to {cfg["redirect_uri"]}... '
          'It may say it cannot connect - that is fine. Copy the full address '
          'from the address bar and run:  auth-finish "<address>"',
          file=sys.stderr)
    return 0


def cmd_auth_finish(args):
    rec = finish_auth(args.callback)
    print(f'logged in. open_id {rec["open_id"]}, scopes {rec["scope"]}')
    return 0


def cmd_whoami(_args):
    info = creator_info(get_access_token())
    print(json.dumps(info, indent=2, ensure_ascii=False))
    return 0


def _spec_from_args(args):
    spec = {'title': args.title, 'privacy': args.privacy, 'draft': args.draft,
            'disable_comment': args.no_comments, 'disable_duet': args.no_duet,
            'disable_stitch': args.no_stitch, 'brand_content': args.paid_partnership,
            'brand_organic': args.own_brand, 'ai': args.ai,
            'cover_ms': args.cover_ms}
    if args.video.startswith(('http://', 'https://')):
        spec['url'] = args.video
    else:
        spec['video'] = args.video
    return spec


def cmd_post(args):
    if args.title_file:
        args.title = Path(args.title_file).read_text(encoding='utf-8').strip()
    spec = _spec_from_args(args)
    if args.dry_run:
        results, code = run_batch([spec], {}, dry_run=True, skip_posted=False)
        return code
    rec = post_one(spec, wait=not args.no_wait)
    print(json.dumps({k: v for k, v in rec.items() if k != 'sha256'},
                     indent=2, ensure_ascii=False))
    if rec.get('fail_reason') in CAP_CODES:
        return EXIT_CAP
    return 1 if rec['result'] == 'FAILED' else 0


def cmd_batch(args):
    specs = load_batch(args.file)
    defaults = {}
    if args.privacy:
        defaults['privacy'] = args.privacy
    if args.draft:
        defaults['draft'] = True
    if args.ai:
        defaults['ai'] = True
    results, code = run_batch(specs, defaults, dry_run=args.dry_run,
                              skip_posted=not args.no_skip, wait=not args.no_wait)
    print(json.dumps(results))
    return code


def cmd_status(args):
    print(json.dumps(fetch_status(get_access_token(), args.publish_id), indent=2))
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        description='Post videos to TikTok via the official Content Posting API.')
    p.add_argument('--check', action='store_true', help='report configuration')
    sub = p.add_subparsers(dest='cmd')

    s = sub.add_parser('setup', help='store client key and secret')
    s.add_argument('--client-key')
    s.add_argument('--redirect-uri')
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser('auth', help='log in (opens a localhost listener)')
    s.add_argument('--timeout', type=int, default=300)
    s.set_defaults(fn=cmd_auth)
    sub.add_parser('auth-url', help='print the login URL').set_defaults(fn=cmd_auth_url)
    s = sub.add_parser('auth-finish', help='finish login with the redirected URL')
    s.add_argument('callback')
    s.set_defaults(fn=cmd_auth_finish)

    sub.add_parser('whoami', help='creator info').set_defaults(fn=cmd_whoami)

    s = sub.add_parser('post', help='post one video')
    s.add_argument('video', help='MP4/MOV/WebM file, or a URL on a verified domain')
    s.add_argument('--title', default='', help='caption; #hashtags and @mentions work')
    s.add_argument('--title-file', help='read the caption from a UTF-8 file')
    s.add_argument('--privacy', type=str.upper, choices=PRIVACY_LEVELS)
    s.add_argument('--draft', action='store_true',
                   help='send to the TikTok app inbox; you finish posting there')
    s.add_argument('--no-comments', action='store_true')
    s.add_argument('--no-duet', action='store_true')
    s.add_argument('--no-stitch', action='store_true')
    s.add_argument('--cover-ms', type=int, help='cover frame, ms into the video')
    s.add_argument('--ai', action='store_true', help='label as AI-generated')
    s.add_argument('--own-brand', action='store_true',
                   help='disclose: promotes your own business')
    s.add_argument('--paid-partnership', action='store_true',
                   help='disclose: branded content for a third party')
    s.add_argument('--no-wait', action='store_true', help='do not poll status')
    s.add_argument('--dry-run', action='store_true', help='validate only')
    s.set_defaults(fn=cmd_post)

    s = sub.add_parser('batch', help='post every row of a JSON or CSV file')
    s.add_argument('file')
    s.add_argument('--privacy', type=str.upper, choices=PRIVACY_LEVELS,
                   help='default for rows that do not set one')
    s.add_argument('--draft', action='store_true')
    s.add_argument('--ai', action='store_true')
    s.add_argument('--dry-run', action='store_true')
    s.add_argument('--no-skip', action='store_true',
                   help='post even files already posted successfully')
    s.add_argument('--no-wait', action='store_true')
    s.set_defaults(fn=cmd_batch)

    s = sub.add_parser('status', help='status of a publish_id')
    s.add_argument('publish_id')
    s.set_defaults(fn=cmd_status)
    return p


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    p = build_parser()
    args = p.parse_args(argv)
    if args.check:
        return cmd_check(args)
    if not getattr(args, 'fn', None):
        p.print_help()
        return 2
    try:
        return args.fn(args)
    except ConfigError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    except TikTokError as e:
        print(f'TikTok error: {e}', file=sys.stderr)
        if e.code in CAP_CODES:
            print('This is TikTok\'s daily limit; try again tomorrow.',
                  file=sys.stderr)
            return EXIT_CAP
        if e.code in ('access_token_invalid', 'invalid_grant'):
            print('The login is no longer valid. Run "auth" (or "auth-url" '
                  'and "auth-finish") again.', file=sys.stderr)
        if e.code == 'scope_not_authorized':
            print('The app lacks video.publish/video.upload. Add the Content '
                  'Posting API product in the developer portal, then log in '
                  'again.', file=sys.stderr)
        if e.code == 'unaudited_client_can_only_post_to_private_accounts':
            print('Your app is not audited yet: set the TikTok account to '
                  'private and post with --privacy SELF_ONLY, or apply for the '
                  'audit in the developer portal.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
