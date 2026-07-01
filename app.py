#!/usr/bin/env python3
"""
GitHub Manager Pro - Web Application
Full-featured GitHub management tool deployable on Render
"""

import os
import re
import difflib
import base64
import requests
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'github-manager-secret-key-change-in-prod')
CORS(app)

GITHUB_API = "https://api.github.com"


# ─── Helpers ──────────────────────────────────────────────────────────────────

def github_session(token):
    s = requests.Session()
    s.headers.update({
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json'
    })
    return s


def require_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('X-GitHub-Token') or request.json and request.json.get('token')
        if not token:
            token = request.args.get('token')
        if not token:
            return jsonify({'error': 'No GitHub token provided'}), 401
        return f(token, *args, **kwargs)
    return decorated


def fuzzy_score(term, text):
    """Lightweight 'semantic' similarity: sequence ratio + token overlap.
    No ML dependency required, so it stays fast and safe to run on a
    free-tier Render instance."""
    if not term or not text:
        return 0.0
    term_l, text_l = term.lower(), text.lower()
    ratio = difflib.SequenceMatcher(None, term_l, text_l).ratio()
    term_tokens = set(re.findall(r'[a-z0-9]+', term_l))
    text_tokens = set(re.findall(r'[a-z0-9]+', text_l))
    overlap = (len(term_tokens & text_tokens) / len(term_tokens)) if term_tokens else 0.0
    # partial substring token match bumps the score too (e.g. "vidrock" in "vidrockapi")
    partial = 1.0 if any(t in text_l for t in term_tokens) else 0.0
    return max(ratio, overlap, partial * 0.6)


def text_matches(term, text, mode):
    if not text:
        return False
    if mode == 'exact':
        return re.search(r'\b' + re.escape(term) + r'\b', text, re.IGNORECASE) is not None
    if mode == 'semantic':
        return fuzzy_score(term, text) >= 0.34
    # partial (default) — plain case-insensitive substring
    return term.lower() in text.lower()


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/user', methods=['POST'])
def get_user():
    data = request.json or {}
    token = data.get('token')
    if not token:
        return jsonify({'error': 'Token required'}), 400
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/user')
    if resp.status_code != 200:
        return jsonify({'error': 'Invalid token or unauthorized'}), 401
    return jsonify(resp.json())


@app.route('/api/repos', methods=['POST'])
def get_repos():
    data = request.json or {}
    token = data.get('token')
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/user/repos', params={
        'per_page': 100, 'sort': 'updated', 'type': 'all'
    })
    if not resp.ok:
        return jsonify({'error': resp.json().get('message', 'Failed to fetch repos')}), resp.status_code
    repos = resp.json()
    # Enrich with language colors
    return jsonify({'repos': repos, 'total': len(repos)})


@app.route('/api/repos/create', methods=['POST'])
def create_repo():
    data = request.json or {}
    token = data.get('token')
    s = github_session(token)
    payload = {
        'name': data.get('name'),
        'description': data.get('description', ''),
        'private': data.get('private', False),
        'auto_init': data.get('auto_init', True),
        'gitignore_template': data.get('gitignore_template', ''),
        'license_template': data.get('license_template', '')
    }
    resp = s.post(f'{GITHUB_API}/user/repos', json=payload)
    if not resp.ok:
        return jsonify({'error': resp.json().get('message', 'Failed to create repo')}), resp.status_code
    return jsonify(resp.json())


@app.route('/api/repos/delete', methods=['POST'])
def delete_repo():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    s = github_session(token)
    resp = s.delete(f'{GITHUB_API}/repos/{username}/{repo}')
    if resp.status_code == 204:
        return jsonify({'success': True})
    return jsonify({'error': resp.json().get('message', 'Failed to delete')}), resp.status_code


@app.route('/api/repos/stats', methods=['POST'])
def get_repo_stats():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    s = github_session(token)
    
    # Fetch repo details, languages, contributors
    repo_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}')
    langs_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/languages')
    commits_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/commits', params={'per_page': 5})
    branches_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/branches')
    
    return jsonify({
        'repo': repo_resp.json() if repo_resp.ok else {},
        'languages': langs_resp.json() if langs_resp.ok else {},
        'recent_commits': commits_resp.json() if commits_resp.ok else [],
        'branches': branches_resp.json() if branches_resp.ok else []
    })


@app.route('/api/files', methods=['POST'])
def get_files():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    path = data.get('path', '')
    s = github_session(token)
    url = f'{GITHUB_API}/repos/{username}/{repo}/contents/{path}'
    resp = s.get(url)
    if not resp.ok:
        return jsonify({'error': resp.json().get('message', 'Failed to fetch files')}), resp.status_code
    return jsonify({'contents': resp.json()})


@app.route('/api/files/get', methods=['POST'])
def get_file_content():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    path = data.get('path')
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/contents/{path}')
    if not resp.ok:
        return jsonify({'error': 'Failed to fetch file'}), resp.status_code
    file_data = resp.json()
    content = ''
    if file_data.get('encoding') == 'base64':
        content = base64.b64decode(file_data['content']).decode('utf-8', errors='replace')
    return jsonify({'content': content, 'sha': file_data.get('sha'), 'name': file_data.get('name')})


@app.route('/api/files/save', methods=['POST'])
def save_file():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    path = data.get('path')
    content = data.get('content', '')
    message = data.get('message', 'Update via GitHub Manager Pro')
    sha = data.get('sha')

    content_b64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    payload = {'message': message, 'content': content_b64}
    if sha:
        payload['sha'] = sha

    s = github_session(token)
    resp = s.put(f'{GITHUB_API}/repos/{username}/{repo}/contents/{path}', json=payload)
    if not resp.ok:
        return jsonify({'error': resp.json().get('message', 'Save failed')}), resp.status_code
    return jsonify({'success': True, 'data': resp.json()})


@app.route('/api/files/delete', methods=['POST'])
def delete_file():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    path = data.get('path')
    sha = data.get('sha')
    message = data.get('message', 'Delete via GitHub Manager Pro')
    s = github_session(token)
    resp = s.delete(f'{GITHUB_API}/repos/{username}/{repo}/contents/{path}',
                    json={'message': message, 'sha': sha})
    if resp.status_code == 200:
        return jsonify({'success': True})
    return jsonify({'error': 'Delete failed'}), resp.status_code


@app.route('/api/workflows', methods=['POST'])
def get_workflows():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    s = github_session(token)
    wf_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/actions/workflows')
    runs_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/actions/runs', params={'per_page': 15})
    return jsonify({
        'workflows': wf_resp.json().get('workflows', []) if wf_resp.ok else [],
        'runs': runs_resp.json().get('workflow_runs', []) if runs_resp.ok else []
    })


@app.route('/api/workflows/trigger', methods=['POST'])
def trigger_workflow():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    workflow_id = data.get('workflow_id')
    ref = data.get('ref', 'main')
    s = github_session(token)
    resp = s.post(
        f'{GITHUB_API}/repos/{username}/{repo}/actions/workflows/{workflow_id}/dispatches',
        json={'ref': ref}
    )
    if resp.status_code == 204:
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to trigger workflow'}), resp.status_code


@app.route('/api/secrets', methods=['POST'])
def get_secrets():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/actions/secrets')
    if not resp.ok:
        return jsonify({'error': 'Failed to fetch secrets'}), resp.status_code
    return jsonify(resp.json())


@app.route('/api/secrets/add', methods=['POST'])
def add_secret():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    secret_name = data.get('secret_name')
    secret_value = data.get('secret_value')
    
    if not all([token, username, repo, secret_name, secret_value]):
        return jsonify({'error': 'Missing required parameters'}), 400
    
    s = github_session(token)
    
    # Get the public key for encryption
    key_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/actions/secrets/public-key')
    if not key_resp.ok:
        return jsonify({'error': 'Failed to get public key'}), key_resp.status_code
    
    public_key_data = key_resp.json()
    
    # Encrypt the secret using the public key
    try:
        from base64 import b64encode
        from nacl import encoding, public
        
        public_key = public.PublicKey(public_key_data['key'].encode('utf-8'), encoding.Base64Encoder())
        sealed_box = public.SealedBox(public_key)
        encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
        encrypted_value = b64encode(encrypted).decode('utf-8')
    except ImportError:
        # If PyNaCl is not available, return error
        return jsonify({'error': 'PyNaCl library required for secret encryption. Install with: pip install pynacl'}), 500
    except Exception as e:
        return jsonify({'error': f'Encryption failed: {str(e)}'}), 500
    
    # Create or update the secret
    payload = {
        'encrypted_value': encrypted_value,
        'key_id': public_key_data['key_id']
    }
    
    resp = s.put(f'{GITHUB_API}/repos/{username}/{repo}/actions/secrets/{secret_name}', json=payload)
    if resp.status_code in [201, 204]:
        return jsonify({'success': True, 'message': f'Secret {secret_name} added successfully'})
    return jsonify({'error': resp.json().get('message', 'Failed to add secret')}), resp.status_code


@app.route('/api/secrets/delete', methods=['POST'])
def delete_secret():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    secret_name = data.get('secret_name')
    
    s = github_session(token)
    resp = s.delete(f'{GITHUB_API}/repos/{username}/{repo}/actions/secrets/{secret_name}')
    if resp.status_code == 204:
        return jsonify({'success': True})
    return jsonify({'error': 'Failed to delete secret'}), resp.status_code


@app.route('/api/issues', methods=['POST'])
def get_issues():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    state = data.get('state', 'open')
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/issues',
                 params={'state': state, 'per_page': 30})
    if not resp.ok:
        return jsonify({'error': 'Failed to fetch issues'}), resp.status_code
    return jsonify({'issues': resp.json()})


@app.route('/api/issues/create', methods=['POST'])
def create_issue():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    s = github_session(token)
    resp = s.post(f'{GITHUB_API}/repos/{username}/{repo}/issues', json={
        'title': data.get('title'),
        'body': data.get('body', ''),
        'labels': data.get('labels', [])
    })
    if not resp.ok:
        return jsonify({'error': 'Failed to create issue'}), resp.status_code
    return jsonify(resp.json())


@app.route('/api/commits', methods=['POST'])
def get_commits():
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    branch = data.get('branch', 'main')
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/commits',
                 params={'sha': branch, 'per_page': 20})
    if not resp.ok:
        return jsonify({'error': 'Failed to fetch commits'}), resp.status_code
    return jsonify({'commits': resp.json()})


# ─── GitHub Pages Management ──────────────────────────────────────────────────

@app.route('/api/pages/status', methods=['POST'])
def get_pages_status():
    """Get GitHub Pages status for a repository"""
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/pages')
    
    if resp.status_code == 404:
        return jsonify({'enabled': False, 'message': 'GitHub Pages not enabled'})
    elif not resp.ok:
        return jsonify({'error': 'Failed to get Pages status'}), resp.status_code
    
    pages_data = resp.json()
    return jsonify({
        'enabled': True,
        'url': pages_data.get('html_url'),
        'status': pages_data.get('status'),
        'source': pages_data.get('source'),
        'custom_domain': pages_data.get('cname'),
        'https_enforced': pages_data.get('https_enforced', False)
    })


@app.route('/api/pages/enable', methods=['POST'])
def enable_pages():
    """Enable GitHub Pages for a repository"""
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    branch = data.get('branch', 'main')
    path = data.get('path', '/')  # '/' or '/docs'
    
    s = github_session(token)
    
    # Create or update GitHub Pages
    payload = {
        'source': {
            'branch': branch,
            'path': path
        }
    }
    
    resp = s.post(f'{GITHUB_API}/repos/{username}/{repo}/pages', json=payload)
    
    if resp.status_code in [201, 204, 409]:
        # 409 means Pages already exists, try to update it
        if resp.status_code == 409:
            update_resp = s.put(f'{GITHUB_API}/repos/{username}/{repo}/pages', json=payload)
            if update_resp.ok or update_resp.status_code == 204:
                return jsonify({'success': True, 'message': 'GitHub Pages updated successfully'})
            return jsonify({'error': 'Failed to update Pages'}), update_resp.status_code
        
        return jsonify({'success': True, 'message': 'GitHub Pages enabled successfully'})
    
    return jsonify({'error': resp.json().get('message', 'Failed to enable Pages')}), resp.status_code


@app.route('/api/pages/disable', methods=['POST'])
def disable_pages():
    """Disable GitHub Pages for a repository"""
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    
    s = github_session(token)
    resp = s.delete(f'{GITHUB_API}/repos/{username}/{repo}/pages')
    
    if resp.status_code == 204:
        return jsonify({'success': True, 'message': 'GitHub Pages disabled'})
    return jsonify({'error': 'Failed to disable Pages'}), resp.status_code


@app.route('/api/pages/builds', methods=['POST'])
def get_pages_builds():
    """Get GitHub Pages build history"""
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/pages/builds')
    
    if not resp.ok:
        return jsonify({'error': 'Failed to get builds'}), resp.status_code
    
    return jsonify({'builds': resp.json()})


@app.route('/api/pages/build', methods=['POST'])
def request_pages_build():
    """Request a new GitHub Pages build"""
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    
    s = github_session(token)
    resp = s.post(f'{GITHUB_API}/repos/{username}/{repo}/pages/builds')
    
    if resp.status_code == 201:
        return jsonify({'success': True, 'message': 'Build requested successfully'})
    return jsonify({'error': 'Failed to request build'}), resp.status_code


# ─── Multi-Account Management ─────────────────────────────────────────────────

@app.route('/api/accounts/verify', methods=['POST'])
def verify_account():
    """Verify a GitHub account using token and username"""
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    
    if not token:
        return jsonify({'error': 'Token required'}), 400
    
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/user')
    
    if resp.status_code != 200:
        return jsonify({'error': 'Invalid token or unauthorized'}), 401
    
    user_data = resp.json()
    actual_username = user_data.get('login')
    
    # If username provided, verify it matches
    if username and username.lower() != actual_username.lower():
        return jsonify({
            'warning': f'Username mismatch: token belongs to @{actual_username}, not @{username}',
            'actual_username': actual_username,
            'user_data': user_data
        })
    
    return jsonify({
        'success': True,
        'username': actual_username,
        'user_data': user_data
    })


@app.route('/api/accounts/repos', methods=['POST'])
def get_account_repos():
    """Get repositories for any GitHub account (including organizations)"""
    data = request.json or {}
    token = data.get('token')
    target_username = data.get('target_username')
    
    s = github_session(token)
    
    # If target username specified, get their public repos
    if target_username:
        resp = s.get(f'{GITHUB_API}/users/{target_username}/repos', params={
            'per_page': 100, 'sort': 'updated', 'type': 'all'
        })
    else:
        # Get authenticated user's repos
        resp = s.get(f'{GITHUB_API}/user/repos', params={
            'per_page': 100, 'sort': 'updated', 'type': 'all'
        })
    
    if not resp.ok:
        return jsonify({'error': resp.json().get('message', 'Failed to fetch repos')}), resp.status_code
    
    repos = resp.json()
    return jsonify({'repos': repos, 'total': len(repos)})


@app.route('/api/accounts/organizations', methods=['POST'])
def get_organizations():
    """Get organizations for the authenticated user"""
    data = request.json or {}
    token = data.get('token')
    
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/user/orgs')
    
    if not resp.ok:
        return jsonify({'error': 'Failed to fetch organizations'}), resp.status_code
    
    return jsonify({'organizations': resp.json()})


@app.route('/api/accounts/org-repos', methods=['POST'])
def get_org_repos():
    """Get repositories for an organization"""
    data = request.json or {}
    token = data.get('token')
    org_name = data.get('org_name')
    
    if not org_name:
        return jsonify({'error': 'Organization name required'}), 400
    
    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/orgs/{org_name}/repos', params={
        'per_page': 100, 'sort': 'updated'
    })
    
    if not resp.ok:
        return jsonify({'error': 'Failed to fetch organization repos'}), resp.status_code
    
    return jsonify({'repos': resp.json()})


# ─── Advanced Search ──────────────────────────────────────────────────────────

@app.route('/api/search/repos', methods=['POST'])
def search_repos():
    """Search repo names / descriptions / full_name for one account."""
    data = request.json or {}
    token = data.get('token')
    org = data.get('org')  # optional: search an org's repos instead of the user's
    term = data.get('term', '').strip()
    mode = data.get('mode', 'partial')
    if not token:
        return jsonify({'error': 'Token required'}), 400
    if not term:
        return jsonify({'error': 'Search term required'}), 400

    s = github_session(token)
    if org:
        resp = s.get(f'{GITHUB_API}/orgs/{org}/repos', params={'per_page': 100, 'sort': 'updated'})
    else:
        resp = s.get(f'{GITHUB_API}/user/repos', params={'per_page': 100, 'sort': 'updated', 'type': 'all'})

    if not resp.ok:
        try:
            msg = resp.json().get('message', 'Failed to fetch repos')
        except Exception:
            msg = 'Failed to fetch repos'
        return jsonify({'error': msg}), resp.status_code

    repos = resp.json()
    matches = []
    for r in repos:
        haystack = ' '.join(filter(None, [r.get('name'), r.get('description'), r.get('full_name')]))
        if text_matches(term, haystack, mode):
            matches.append({
                'name': r.get('name'),
                'full_name': r.get('full_name'),
                'description': r.get('description'),
                'html_url': r.get('html_url'),
                'stargazers_count': r.get('stargazers_count'),
                'language': r.get('language'),
                'private': r.get('private'),
                'updated_at': r.get('updated_at'),
            })
    return jsonify({'matches': matches, 'scanned': len(repos)})


@app.route('/api/search/code', methods=['POST'])
def search_code():
    """Proxy to GitHub's indexed code search (fast, but rate-limited to ~10 req/min)."""
    data = request.json or {}
    token = data.get('token')
    term = data.get('term', '').strip()
    username = data.get('username')  # scope to a user/org
    repo = data.get('repo')          # scope to a single "owner/name"
    mode = data.get('mode', 'partial')
    page = int(data.get('page', 1) or 1)

    if not token:
        return jsonify({'error': 'Token required'}), 400
    if not term:
        return jsonify({'error': 'Search term required'}), 400

    q = f'"{term}"' if mode == 'exact' else term
    if repo:
        q += f' repo:{repo}'
    elif username:
        q += f' user:{username}'

    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/search/code', params={'q': q, 'per_page': 50, 'page': page})

    if not resp.ok:
        try:
            msg = resp.json().get('message', 'Code search failed')
        except Exception:
            msg = 'Code search failed'
        return jsonify({'error': msg}), resp.status_code

    result = resp.json()
    items = [{
        'name': it.get('name'),
        'path': it.get('path'),
        'repository': (it.get('repository') or {}).get('full_name'),
        'html_url': it.get('html_url'),
        'sha': it.get('sha'),
    } for it in result.get('items', [])]

    return jsonify({
        'items': items,
        'total_count': result.get('total_count', 0),
        'incomplete_results': result.get('incomplete_results', False),
        'page': page,
    })


@app.route('/api/repos/tree', methods=['POST'])
def get_repo_tree():
    """Full recursive file listing for a repo — used to size the progress bar
    before a deep scan, and to enumerate candidate files to grep."""
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    branch = data.get('branch')

    if not all([token, username, repo]):
        return jsonify({'error': 'token, username and repo are required'}), 400

    s = github_session(token)

    if not branch:
        repo_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}')
        if not repo_resp.ok:
            return jsonify({'error': 'Failed to fetch repo info'}), repo_resp.status_code
        branch = repo_resp.json().get('default_branch', 'main')

    tree_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/git/trees/{branch}', params={'recursive': 1})
    if not tree_resp.ok:
        try:
            msg = tree_resp.json().get('message', 'Failed to fetch tree')
        except Exception:
            msg = 'Failed to fetch tree'
        return jsonify({'error': msg}), tree_resp.status_code

    tree_data = tree_resp.json()
    files = [
        {'path': t['path'], 'sha': t['sha'], 'size': t.get('size', 0)}
        for t in tree_data.get('tree', []) if t.get('type') == 'blob'
    ]
    return jsonify({'files': files, 'truncated': tree_data.get('truncated', False), 'branch': branch})


@app.route('/api/repos/blob', methods=['POST'])
def get_blob():
    """Fetch a single file's content by blob sha (one call, no path lookup) —
    used by the deep scan to grep file contents."""
    data = request.json or {}
    token = data.get('token')
    username = data.get('username')
    repo = data.get('repo')
    sha = data.get('sha')

    if not all([token, username, repo, sha]):
        return jsonify({'error': 'token, username, repo and sha are required'}), 400

    s = github_session(token)
    resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/git/blobs/{sha}')
    if not resp.ok:
        return jsonify({'error': 'Failed to fetch blob'}), resp.status_code

    blob = resp.json()
    content = ''
    if blob.get('encoding') == 'base64':
        try:
            content = base64.b64decode(blob['content']).decode('utf-8', errors='replace')
        except Exception:
            content = ''
    return jsonify({'content': content, 'size': blob.get('size', 0)})


# ─── Batch Operations ─────────────────────────────────────────────────────────

@app.route('/api/batch/enable-pages', methods=['POST'])
def batch_enable_pages():
    """Enable GitHub Pages for multiple repositories"""
    data = request.json or {}
    token = data.get('token')
    repositories = data.get('repositories', [])  # [{'username': 'x', 'repo': 'y', 'branch': 'main', 'path': '/'}]
    
    results = []
    s = github_session(token)
    
    for repo_config in repositories:
        username = repo_config.get('username')
        repo = repo_config.get('repo')
        branch = repo_config.get('branch', 'main')
        path = repo_config.get('path', '/')
        
        payload = {
            'source': {
                'branch': branch,
                'path': path
            }
        }
        
        resp = s.post(f'{GITHUB_API}/repos/{username}/{repo}/pages', json=payload)
        
        if resp.status_code in [201, 204]:
            results.append({'repo': f'{username}/{repo}', 'status': 'success'})
        elif resp.status_code == 409:
            # Try to update
            update_resp = s.put(f'{GITHUB_API}/repos/{username}/{repo}/pages', json=payload)
            if update_resp.ok or update_resp.status_code == 204:
                results.append({'repo': f'{username}/{repo}', 'status': 'updated'})
            else:
                results.append({'repo': f'{username}/{repo}', 'status': 'failed', 'error': 'Update failed'})
        else:
            results.append({'repo': f'{username}/{repo}', 'status': 'failed', 'error': resp.json().get('message', 'Unknown error')})
    
    return jsonify({'results': results})


@app.route('/api/batch/add-secrets', methods=['POST'])
def batch_add_secrets():
    """Add the same secret to multiple repositories"""
    data = request.json or {}
    token = data.get('token')
    repositories = data.get('repositories', [])  # [{'username': 'x', 'repo': 'y'}]
    secret_name = data.get('secret_name')
    secret_value = data.get('secret_value')
    
    if not all([secret_name, secret_value]):
        return jsonify({'error': 'Secret name and value required'}), 400
    
    results = []
    s = github_session(token)
    
    try:
        from base64 import b64encode
        from nacl import encoding, public
    except ImportError:
        return jsonify({'error': 'PyNaCl library required. Install with: pip install pynacl'}), 500
    
    for repo_config in repositories:
        username = repo_config.get('username')
        repo = repo_config.get('repo')
        
        try:
            # Get public key
            key_resp = s.get(f'{GITHUB_API}/repos/{username}/{repo}/actions/secrets/public-key')
            if not key_resp.ok:
                results.append({'repo': f'{username}/{repo}', 'status': 'failed', 'error': 'Failed to get public key'})
                continue
            
            public_key_data = key_resp.json()
            
            # Encrypt secret
            public_key_obj = public.PublicKey(public_key_data['key'].encode('utf-8'), encoding.Base64Encoder())
            sealed_box = public.SealedBox(public_key_obj)
            encrypted = sealed_box.encrypt(secret_value.encode('utf-8'))
            encrypted_value = b64encode(encrypted).decode('utf-8')
            
            # Add secret
            payload = {
                'encrypted_value': encrypted_value,
                'key_id': public_key_data['key_id']
            }
            
            resp = s.put(f'{GITHUB_API}/repos/{username}/{repo}/actions/secrets/{secret_name}', json=payload)
            if resp.status_code in [201, 204]:
                results.append({'repo': f'{username}/{repo}', 'status': 'success'})
            else:
                results.append({'repo': f'{username}/{repo}', 'status': 'failed', 'error': resp.json().get('message', 'Unknown error')})
        
        except Exception as e:
            results.append({'repo': f'{username}/{repo}', 'status': 'failed', 'error': str(e)})
    
    return jsonify({'results': results})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('DEBUG', 'false').lower() == 'true')
