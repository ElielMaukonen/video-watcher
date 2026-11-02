"""Tests for the tiktok-post script.

stdlib unittest, no network: `_request` is replaced with a fake TikTok that
records every call, so the OAuth, init, chunked upload and status flows run
end to end against the documented request/response shapes.

    py -m unittest discover -s tests
"""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.parse
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / '.claude' / 'skills' / 'tiktok-post' / 'scripts' / 'tiktok_post.py'

_spec = importlib.util.spec_from_file_location('tiktok_post', SCRIPT)
tp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tp)

MB = 1024 * 1024
INFO = {'creator_username': 'me', 'max_video_post_duration_sec': 180,
        'privacy_level_options': ['PUBLIC_TO_EVERYONE', 'SELF_ONLY'],
        'comment_disabled': False, 'duet_disabled': True,
        'stitch_disabled': False}


def ok(data):
    return 200, json.dumps({'data': data, 'error': {'code': 'ok'}}).encode()


def err(code, status=200):
    return status, json.dumps({'data': {}, 'error': {
        'code': code, 'message': code, 'log_id': 'L1'}}).encode()


class FakeTikTok:
    """Answers the endpoints the script uses and records each request."""

    def __init__(self):
        self.calls = []
        self.init_error = None
        self.final_status = 'PUBLISH_COMPLETE'
        self.fail_reason = None
        self.refresh_count = 0

    def __call__(self, url, data=None, headers=None, method=None, timeout=120):
        headers = headers or {}
        self.calls.append({'url': url, 'data': data, 'headers': headers,
                           'method': method})
        if url.endswith('/v2/oauth/token/'):
            form = urllib.parse.parse_qs(data.decode())
            if form['grant_type'][0] == 'refresh_token':
                self.refresh_count += 1
            return 200, json.dumps({
                'access_token': f'AT{self.refresh_count}',
                'expires_in': 7200,
                'refresh_token': f'RT{self.refresh_count}',
                'refresh_expires_in': 15552000,
                'open_id': 'OPEN', 'scope': 'user.info.basic,video.publish,video.upload',
                'token_type': 'Bearer'}).encode()
        if url.endswith('/creator_info/query/'):
            return ok(INFO)
        if url.endswith('/video/init/'):
            if self.init_error:
                return err(self.init_error, 403)
            return ok({'publish_id': 'v_pub_1',
                       'upload_url': 'https://upload.example/abc?x=1'})
        if url.startswith('https://upload.example/'):
            return 201, b''
        if url.endswith('/status/fetch/'):
            d = {'status': self.final_status}
            if self.fail_reason:
                d['fail_reason'] = self.fail_reason
            return ok(d)
        return 404, b'{}'

    def by(self, suffix):
        return [c for c in self.calls if c['url'].endswith(suffix)]

    def puts(self):
        return [c for c in self.calls if c['method'] == 'PUT']


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = {k: os.environ.get(k) for k in (
            'TIKTOK_HOME', 'TIKTOK_CLIENT_KEY', 'TIKTOK_CLIENT_SECRET',
            'TIKTOK_REDIRECT_URI')}
        os.environ['TIKTOK_HOME'] = str(self.tmp / 'home')
        os.environ['TIKTOK_CLIENT_KEY'] = 'CK'
        os.environ['TIKTOK_CLIENT_SECRET'] = 'CS'
        os.environ.pop('TIKTOK_REDIRECT_URI', None)
        self.addCleanup(self._restore_env)
        self.fake = FakeTikTok()
        self._patch('_request', self.fake)
        self._patch('probe_duration', lambda p: 30.0)
        self._patch('INIT_SPACING_S', 0)
        self._patch('STATUS_POLL_S', 0)

    def _restore_env(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _patch(self, name, value):
        old = getattr(tp, name)
        setattr(tp, name, value)
        self.addCleanup(setattr, tp, name, old)

    def video(self, size, name='clip.mp4'):
        p = self.tmp / name
        with open(p, 'wb') as f:
            f.write(os.urandom(min(size, 4096)))
            if size > 4096:
                f.seek(size - 1)
                f.write(b'\0')
        return p

    def login(self):
        tp._write_private(tp.home() / 'token.json', {
            'access_token': 'AT', 'expires_at': time.time() + 7000,
            'refresh_token': 'RT', 'refresh_expires_at': time.time() + 1e7,
            'open_id': 'OPEN', 'scope': 'video.publish,video.upload'})

    def quiet(self, fn, *a, **kw):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return fn(*a, **kw)


# ----------------------------------------------------------------- chunks ----
class TestChunks(unittest.TestCase):
    def test_small_file_is_one_chunk_of_its_own_size(self):
        self.assertEqual(tp.chunk_plan(3 * MB), (3 * MB, 1))

    def test_up_to_64mb_is_one_chunk(self):
        self.assertEqual(tp.chunk_plan(64 * MB), (64 * MB, 1))

    def test_large_file_floors_count_and_folds_remainder_into_last(self):
        """TikTok's documented rule: count = floor(size / chunk); the last
        chunk absorbs the remainder (and may be up to 128 MB)."""
        size = 105 * MB + 123
        cs, n = tp.chunk_plan(size)
        self.assertEqual((cs, n), (10 * MB, 10))
        ranges = tp.chunk_ranges(size, cs, n)
        self.assertEqual(ranges[0], (0, 10 * MB - 1))
        self.assertEqual(ranges[-1], (90 * MB, size - 1))
        self.assertEqual(sum(e - s + 1 for s, e in ranges), size)
        last = ranges[-1][1] - ranges[-1][0] + 1
        self.assertLessEqual(last, 128 * MB)
        self.assertTrue(all(5 * MB <= e - s + 1 <= 64 * MB for s, e in ranges[:-1]))

    def test_chunk_count_stays_within_1000_at_4gb(self):
        self.assertLessEqual(tp.chunk_plan(4 * 1024 * MB)[1], 1000)

    def test_rejects_empty_and_oversize(self):
        with self.assertRaises(ValueError):
            tp.chunk_plan(0)
        with self.assertRaises(ValueError):
            tp.chunk_plan(4 * 1024 * MB + 1)


# ------------------------------------------------------------------ oauth ----
class TestOAuth(Base):
    def test_pkce_challenge_is_hex_sha256(self):
        v, c = tp.pkce_pair()
        self.assertEqual(c, hashlib.sha256(v.encode()).hexdigest())
        self.assertTrue(43 <= len(v) <= 128)

    def test_auth_url_carries_required_params(self):
        _cfg, state, url = tp.start_auth()
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q['client_key'], ['CK'])
        self.assertEqual(q['response_type'], ['code'])
        self.assertEqual(q['code_challenge_method'], ['S256'])
        self.assertEqual(q['state'], [state])
        self.assertIn('video.publish', q['scope'][0])
        self.assertIn('video.upload', q['scope'][0])
        self.assertNotIn('CS', url)  # never leak the secret

    def test_parse_callback_accepts_full_url_and_bare_query(self):
        self.assertEqual(tp.parse_callback(
            '"http://localhost:3455/callback/?code=ab%2Ac&scopes=x&state=S"'),
            ('ab*c', 'S'))
        self.assertEqual(tp.parse_callback('?code=Z&state=Q'), ('Z', 'Q'))

    def test_parse_callback_surfaces_tiktok_error(self):
        with self.assertRaises(tp.TikTokError) as cm:
            tp.parse_callback('http://localhost:3455/callback/?error=access_denied'
                              '&error_description=User+cancelled')
        self.assertEqual(cm.exception.code, 'access_denied')

    def test_finish_exchanges_code_with_verifier_and_saves_token(self):
        _cfg, state, _url = tp.start_auth()
        verifier = json.loads((tp.home() / 'pending_auth.json').read_text())['verifier']
        rec = tp.finish_auth(f'http://localhost:3455/callback/?code=C1&state={state}')
        form = urllib.parse.parse_qs(self.fake.by('/oauth/token/')[0]['data'].decode())
        self.assertEqual(form['code'], ['C1'])
        self.assertEqual(form['code_verifier'], [verifier])
        self.assertEqual(form['grant_type'], ['authorization_code'])
        self.assertEqual(form['redirect_uri'], [tp.DEFAULT_REDIRECT])
        self.assertEqual(rec['refresh_token'], 'RT0')
        self.assertFalse((tp.home() / 'pending_auth.json').exists())
        if os.name == 'posix':
            self.assertEqual((tp.home() / 'token.json').stat().st_mode & 0o777, 0o600)

    def test_finish_rejects_wrong_state(self):
        tp.start_auth()
        with self.assertRaises(tp.ConfigError):
            tp.finish_auth('http://localhost:3455/callback/?code=C1&state=other')
        self.assertEqual(self.fake.calls, [])

    def test_expired_access_token_refreshes_and_stores_rotated_refresh_token(self):
        """Refresh tokens rotate: keeping the old one bricks the login."""
        self.login()
        tok = json.loads((tp.home() / 'token.json').read_text())
        tok['expires_at'] = time.time() - 1
        tp._write_private(tp.home() / 'token.json', tok)
        self.assertEqual(tp.get_access_token(), 'AT1')
        saved = json.loads((tp.home() / 'token.json').read_text())
        self.assertEqual(saved['refresh_token'], 'RT1')
        form = urllib.parse.parse_qs(self.fake.by('/oauth/token/')[0]['data'].decode())
        self.assertEqual(form['refresh_token'], ['RT'])

    def test_fresh_token_makes_no_request(self):
        self.login()
        self.assertEqual(tp.get_access_token(), 'AT')
        self.assertEqual(self.fake.calls, [])

    def test_missing_credentials_is_a_config_error(self):
        os.environ.pop('TIKTOK_CLIENT_KEY')
        with self.assertRaises(tp.ConfigError):
            tp.start_auth()


# --------------------------------------------------------------- validate ----
class TestValidate(unittest.TestCase):
    def test_direct_post_needs_explicit_privacy(self):
        self.assertTrue(tp.validate_post({'title': 'x'}, INFO))

    def test_privacy_must_be_one_the_account_offers(self):
        p = tp.validate_post({'privacy': 'FOLLOWER_OF_CREATOR'}, INFO)
        self.assertTrue(p and 'not available' in p[0])

    def test_branded_content_cannot_be_private(self):
        self.assertTrue(tp.validate_post(
            {'privacy': 'SELF_ONLY', 'brand_content': True}, INFO))

    def test_too_long_for_account(self):
        self.assertTrue(tp.validate_post(
            {'privacy': 'SELF_ONLY', '_duration': 200.0}, INFO))

    def test_title_limit_counts_utf16_units(self):
        emoji = '\U0001F600' * 1101  # 2202 UTF-16 units, 1101 code points
        self.assertTrue(tp.validate_post({'privacy': 'SELF_ONLY', 'title': emoji}, INFO))

    def test_draft_needs_no_privacy(self):
        self.assertEqual(tp.validate_post({'draft': True}, INFO), [])

    def test_valid_post(self):
        self.assertEqual(tp.validate_post(
            {'privacy': 'PUBLIC_TO_EVERYONE', 'title': 'hei #fyp',
             '_duration': 30.0}, INFO), [])


# ------------------------------------------------------------------- post ----
class TestPost(Base):
    def test_direct_post_single_chunk_end_to_end(self):
        self.login()
        v = self.video(2 * MB)
        rec = self.quiet(tp.post_one, {'video': str(v), 'title': 'Moi #fyp',
                                       'privacy': 'SELF_ONLY', 'ai': True})
        self.assertEqual(rec['result'], 'PUBLISH_COMPLETE')
        body = json.loads(self.fake.by('/post/publish/video/init/')[0]['data'])
        self.assertEqual(body['source_info'], {
            'source': 'FILE_UPLOAD', 'video_size': 2 * MB,
            'chunk_size': 2 * MB, 'total_chunk_count': 1})
        pi = body['post_info']
        self.assertEqual(pi['privacy_level'], 'SELF_ONLY')
        self.assertEqual(pi['title'], 'Moi #fyp')
        self.assertTrue(pi['is_aigc'])
        self.assertTrue(pi['disable_duet'])  # account setting wins
        put = self.fake.puts()
        self.assertEqual(len(put), 1)
        self.assertEqual(put[0]['url'], 'https://upload.example/abc?x=1')
        self.assertEqual(put[0]['headers']['Content-Range'], f'bytes 0-{2 * MB - 1}/{2 * MB}')
        self.assertEqual(put[0]['headers']['Content-Type'], 'video/mp4')
        log = (tp.home() / 'posts.jsonl').read_text().splitlines()
        self.assertEqual(json.loads(log[0])['publish_id'], 'v_pub_1')

    def test_multi_chunk_upload_covers_every_byte_in_order(self):
        self.login()
        size = 70 * MB + 7
        v = self.video(size, 'big.mov')
        self.quiet(tp.post_one, {'video': str(v), 'privacy': 'SELF_ONLY'})
        puts = self.fake.puts()
        self.assertEqual(len(puts), 7)
        self.assertEqual(sum(len(p['data']) for p in puts), size)
        self.assertEqual(puts[-1]['headers']['Content-Range'],
                         f'bytes {60 * MB}-{size - 1}/{size}')
        self.assertEqual(puts[0]['headers']['Content-Type'], 'video/quicktime')

    def test_draft_uses_inbox_endpoint_without_post_info(self):
        self.login()
        self.fake.final_status = 'SEND_TO_USER_INBOX'
        rec = self.quiet(tp.post_one, {'video': str(self.video(MB)), 'draft': True})
        self.assertEqual(rec['result'], 'SEND_TO_USER_INBOX')
        call = self.fake.by('/inbox/video/init/')
        self.assertEqual(len(call), 1)
        self.assertNotIn('post_info', json.loads(call[0]['data']))

    def test_invalid_post_spends_no_init_call(self):
        self.login()
        with self.assertRaises(tp.ConfigError):
            self.quiet(tp.post_one, {'video': str(self.video(MB))})
        self.assertEqual(self.fake.by('/video/init/'), [])

    def test_unsupported_extension(self):
        self.login()
        with self.assertRaises(tp.ConfigError):
            self.quiet(tp.post_one, {'video': str(self.video(MB, 'x.avi')),
                                     'privacy': 'SELF_ONLY'})

    def test_api_error_carries_code(self):
        self.login()
        self.fake.init_error = 'unaudited_client_can_only_post_to_private_accounts'
        with self.assertRaises(tp.TikTokError) as cm:
            self.quiet(tp.post_one, {'video': str(self.video(MB)),
                                     'privacy': 'PUBLIC_TO_EVERYONE'})
        self.assertEqual(cm.exception.code,
                         'unaudited_client_can_only_post_to_private_accounts')


# ------------------------------------------------------------------ batch ----
class TestBatch(Base):
    def write_batch(self, rows, name='posts.json'):
        p = self.tmp / name
        p.write_text(json.dumps(rows), encoding='utf-8')
        return p

    def test_csv_batch_resolves_relative_paths_and_bools(self):
        self.video(MB, 'a.mp4')
        p = self.tmp / 'posts.csv'
        p.write_text('video,title,privacy,ai\na.mp4,Hei,self_only,kyllä\n',
                     encoding='utf-8')
        specs = tp.load_batch(p)
        self.assertEqual(specs[0]['video'], str(self.tmp / 'a.mp4'))
        self.assertEqual(specs[0]['privacy'], 'SELF_ONLY')
        self.assertIs(specs[0]['ai'], True)

    def test_rerun_skips_already_posted_files(self):
        self.login()
        a, b = self.video(MB, 'a.mp4'), self.video(MB, 'b.mp4')
        specs = tp.load_batch(self.write_batch([{'video': str(a)}, {'video': str(b)}]))
        res, code = self.quiet(tp.run_batch, specs, {'privacy': 'SELF_ONLY'})
        self.assertEqual((res['posted'], code), (2, 0))
        res, code = self.quiet(tp.run_batch, specs, {'privacy': 'SELF_ONLY'})
        self.assertEqual((res['posted'], res['skipped']), (0, 2))
        self.assertEqual(len(self.fake.by('/video/init/')), 2)

    def test_daily_cap_stops_batch_with_exit_3(self):
        self.login()
        self.fake.init_error = 'spam_risk_too_many_posts'
        vids = [self.video(MB, f'{i}.mp4') for i in range(3)]
        specs = tp.load_batch(self.write_batch([{'video': str(v)} for v in vids]))
        res, code = self.quiet(tp.run_batch, specs, {'privacy': 'SELF_ONLY'})
        self.assertEqual(code, tp.EXIT_CAP)
        self.assertEqual(res['remaining'], 3)
        self.assertEqual(len(self.fake.by('/video/init/')), 1)

    def test_dry_run_makes_no_requests(self):
        v = self.video(MB, 'a.mp4')
        specs = tp.load_batch(self.write_batch([
            {'video': str(v), 'privacy': 'SELF_ONLY'},
            {'video': str(self.tmp / 'missing.mp4'), 'privacy': 'SELF_ONLY'}]))
        res, code = self.quiet(tp.run_batch, specs, {}, dry_run=True)
        self.assertEqual((res['valid'], res['failed'], code), (1, 1, 1))
        self.assertEqual(self.fake.calls, [])


class TestRepoHygiene(unittest.TestCase):
    def test_token_dir_is_gitignored(self):
        lines = (REPO / '.gitignore').read_text(encoding='utf-8').splitlines()
        self.assertIn('/.tiktok/', [l.strip() for l in lines])


if __name__ == '__main__':
    unittest.main()
