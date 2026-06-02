#!/usr/bin/env python3
"""
GitHub Manager Pro - Web Application
Full-featured GitHub management tool deployable on Render
"""

import os
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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('DEBUG', 'false').lower() == 'true')
