#!/usr/bin/env python3
"""
GitHub Manager Pro - Web Application
Full-featured GitHub management tool deployable on Render
"""

import os
import base64
import requests
import re
from datetime import datetime
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from functools import wraps, lru_cache
import logging

# Optional: semantic search support
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    from sklearn.metrics.pairwise import cosine_similarity
    SEMANTIC_SEARCH_AVAILABLE = True
    # Lazy load model when needed
    _semantic_model = None
except ImportError:
    SEMANTIC_SEARCH_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'github-manager-secret-key-change-in-prod')
CORS(app)

GITHUB_API = "https://api.github.com"
logging.basicConfig(level=logging.INFO)


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


def get_semantic_model():
    """Lazy load semantic search model"""
    global _semantic_model
    if _semantic_model is None and SEMANTIC_SEARCH_AVAILABLE:
        _semantic_model = SentenceTransformer('all-MiniLM-L6-v2')
    return _semantic_model


def calculate_relevance_score(query, text, exact_match=False):
    """Calculate relevance score between query and text"""
    if not text:
        return 0
    
    text_lower = text.lower()
    query_lower = query.lower()
    
    if exact_match:
        # Exact phrase match
        return 100 if query_lower in text_lower else 0
    
    # Calculate fuzzy match score
    words = query_lower.split()
    matches = sum(1 for word in words if word in text_lower)
    word_score = (matches / len(words)) * 100 if words else 0
    
    # Bonus for exact phrase match
    if query_lower in text_lower:
        word_score = min(100, word_score + 50)
    
    return word_score


def semantic_similarity(query, text):
    """Calculate semantic similarity using sentence transformers"""
    if not SEMANTIC_SEARCH_AVAILABLE or not text:
        return 0
    
    try:
        model = get_semantic_model()
        if model is None:
            return 0
        
        query_embedding = model.encode([query])
        text_embedding = model.encode([text])
        similarity = cosine_similarity(query_embedding, text_embedding)[0][0]
        return float(similarity * 100)
    except Exception as e:
        logging.error(f"Semantic similarity error: {e}")
        return 0


def search_in_file_content(session, username, repo, path, query, search_mode='fuzzy'):
    """Search within a file's content"""
    try:
        resp = session.get(f'{GITHUB_API}/repos/{username}/{repo}/contents/{path}')
        if not resp.ok or resp.json().get('type') != 'file':
            return None
        
        file_data = resp.json()
        if file_data.get('encoding') == 'base64':
            content = base64.b64decode(file_data['content']).decode('utf-8', errors='ignore')
        else:
            content = file_data.get('content', '')
        
        if search_mode == 'exact':
            score = calculate_relevance_score(query, content, exact_match=True)
        elif search_mode == 'semantic':
            score = semantic_similarity(query, content[:5000])  # Limit to first 5000 chars
        else:  # fuzzy
            score = calculate_relevance_score(query, content, exact_match=False)
        
        if score > 0:
            # Find matching lines
            lines = content.split('\n')
            matches = []
            for i, line in enumerate(lines[:1000], 1):  # Limit to first 1000 lines
                if search_mode == 'exact':
                    if query.lower() in line.lower():
                        matches.append({'line': i, 'content': line.strip()})
                elif query.lower() in line.lower() or any(word.lower() in line.lower() for word in query.split()):
                    matches.append({'line': i, 'content': line.strip()})
            
            return {
                'path': path,
                'score': score,
                'matches': matches[:10],  # Limit to 10 matches per file
                'total_matches': len(matches)
            }
        
        return None
    except Exception as e:
        logging.error(f"Error searching file {path}: {e}")
        return None


def search_repository_recursive(session, username, repo, query, search_mode='fuzzy', 
                                file_extensions=None, max_depth=10, current_path='', 
                                current_depth=0):
    """Recursively search through repository files"""
    if current_depth >= max_depth:
        return []
    
    results = []
    
    try:
        resp = session.get(f'{GITHUB_API}/repos/{username}/{repo}/contents/{current_path}')
        if not resp.ok:
            return results
        
        contents = resp.json()
        if not isinstance(contents, list):
            contents = [contents]
        
        for item in contents:
            item_type = item.get('type')
            item_path = item.get('path')
            item_name = item.get('name', '')
            
            # Check file extension filter
            if file_extensions and item_type == 'file':
                ext = os.path.splitext(item_name)[1].lower()
                if ext not in file_extensions:
                    continue
            
            # Search in filename
            filename_score = calculate_relevance_score(query, item_name, 
                                                      exact_match=(search_mode == 'exact'))
            
            if item_type == 'file':
                # Search in file content
                file_result = search_in_file_content(session, username, repo, item_path, 
                                                    query, search_mode)
                if file_result:
                    file_result['filename_score'] = filename_score
                    file_result['total_score'] = (filename_score * 0.3) + (file_result['score'] * 0.7)
                    results.append(file_result)
                elif filename_score > 30:  # Filename match threshold
                    results.append({
                        'path': item_path,
                        'filename_score': filename_score,
                        'score': 0,
                        'total_score': filename_score * 0.3,
                        'matches': [],
                        'total_matches': 0
                    })
            
            elif item_type == 'dir' and current_depth < max_depth - 1:
                # Recurse into directories
                subresults = search_repository_recursive(session, username, repo, query, 
                                                        search_mode, file_extensions, 
                                                        max_depth, item_path, current_depth + 1)
                results.extend(subresults)
        
        return results
    
    except Exception as e:
        logging.error(f"Error searching repository {username}/{repo}: {e}")
        return results


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


# ─── Advanced Search System ───────────────────────────────────────────────────

@app.route('/api/search/repositories', methods=['POST'])
def search_repositories():
    """
    Advanced repository search across single, multiple, or all repositories
    
    Search Options:
    - scope: 'single' | 'selected' | 'all' | 'account'
    - mode: 'fuzzy' | 'exact' | 'semantic'
    - search_in: ['filename', 'content', 'repo_name', 'description']
    - file_extensions: ['.py', '.js', '.md'] (optional filter)
    """
    data = request.json or {}
    token = data.get('token')
    query = data.get('query', '').strip()
    
    if not query:
        return jsonify({'error': 'Search query required'}), 400
    
    # Search configuration
    scope = data.get('scope', 'single')  # single, selected, all, account
    mode = data.get('mode', 'fuzzy')  # fuzzy, exact, semantic
    search_in = data.get('search_in', ['filename', 'content'])
    file_extensions = data.get('file_extensions', [])
    max_results = data.get('max_results', 100)
    
    # Repository targets
    username = data.get('username')
    single_repo = data.get('repo')
    selected_repos = data.get('selected_repos', [])  # [{'username': 'x', 'repo': 'y'}]
    
    s = github_session(token)
    all_results = []
    
    try:
        # Determine which repositories to search
        repos_to_search = []
        
        if scope == 'single' and single_repo:
            repos_to_search = [{'username': username, 'repo': single_repo}]
        
        elif scope == 'selected' and selected_repos:
            repos_to_search = selected_repos
        
        elif scope == 'all' or scope == 'account':
            # Get all accessible repositories
            resp = s.get(f'{GITHUB_API}/user/repos', params={
                'per_page': 100, 'type': 'all', 'sort': 'updated'
            })
            if resp.ok:
                repos_data = resp.json()
                repos_to_search = [
                    {'username': r['owner']['login'], 'repo': r['name']}
                    for r in repos_data
                ]
        
        if not repos_to_search:
            return jsonify({'error': 'No repositories to search'}), 400
        
        # Search each repository
        for repo_info in repos_to_search[:50]:  # Limit to 50 repos max
            repo_username = repo_info['username']
            repo_name = repo_info['repo']
            
            # Search in repository name/description if requested
            if 'repo_name' in search_in or 'description' in search_in:
                repo_resp = s.get(f'{GITHUB_API}/repos/{repo_username}/{repo_name}')
                if repo_resp.ok:
                    repo_data = repo_resp.json()
                    repo_score = 0
                    
                    if 'repo_name' in search_in:
                        repo_score += calculate_relevance_score(query, repo_data.get('name', ''), 
                                                                exact_match=(mode == 'exact'))
                    
                    if 'description' in search_in:
                        repo_score += calculate_relevance_score(query, repo_data.get('description', ''), 
                                                                exact_match=(mode == 'exact'))
                    
                    if repo_score > 30:
                        all_results.append({
                            'type': 'repository',
                            'username': repo_username,
                            'repo': repo_name,
                            'score': repo_score,
                            'name': repo_data.get('name'),
                            'description': repo_data.get('description'),
                            'url': repo_data.get('html_url')
                        })
            
            # Search in files if requested
            if 'filename' in search_in or 'content' in search_in:
                file_results = search_repository_recursive(
                    s, repo_username, repo_name, query, mode, 
                    file_extensions if file_extensions else None, max_depth=5
                )
                
                for result in file_results:
                    result['type'] = 'file'
                    result['username'] = repo_username
                    result['repo'] = repo_name
                    result['url'] = f'https://github.com/{repo_username}/{repo_name}/blob/main/{result["path"]}'
                    all_results.append(result)
        
        # Sort by relevance score
        all_results.sort(key=lambda x: x.get('total_score', x.get('score', 0)), reverse=True)
        
        # Limit results
        all_results = all_results[:max_results]
        
        return jsonify({
            'success': True,
            'query': query,
            'scope': scope,
            'mode': mode,
            'total_results': len(all_results),
            'results': all_results,
            'repositories_searched': len(repos_to_search),
            'semantic_available': SEMANTIC_SEARCH_AVAILABLE
        })
    
    except Exception as e:
        logging.error(f"Search error: {e}")
        return jsonify({'error': f'Search failed: {str(e)}'}), 500


@app.route('/api/search/code', methods=['POST'])
def search_code_github():
    """
    Use GitHub's Code Search API for fast searching
    """
    data = request.json or {}
    token = data.get('token')
    query = data.get('query', '').strip()
    username = data.get('username')
    repo = data.get('repo')
    
    if not query:
        return jsonify({'error': 'Search query required'}), 400
    
    s = github_session(token)
    
    # Build search query
    search_query = query
    if username and repo:
        search_query = f'{query} repo:{username}/{repo}'
    elif username:
        search_query = f'{query} user:{username}'
    
    try:
        resp = s.get(f'{GITHUB_API}/search/code', params={
            'q': search_query,
            'per_page': 50
        })
        
        if not resp.ok:
            return jsonify({'error': resp.json().get('message', 'Search failed')}), resp.status_code
        
        results = resp.json()
        
        return jsonify({
            'success': True,
            'query': query,
            'total_count': results.get('total_count', 0),
            'items': results.get('items', [])
        })
    
    except Exception as e:
        logging.error(f"GitHub code search error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/issues', methods=['POST'])
def search_issues():
    """
    Search issues across repositories
    """
    data = request.json or {}
    token = data.get('token')
    query = data.get('query', '').strip()
    username = data.get('username')
    repo = data.get('repo')
    state = data.get('state', 'all')  # open, closed, all
    
    if not query:
        return jsonify({'error': 'Search query required'}), 400
    
    s = github_session(token)
    
    # Build search query
    search_query = f'{query} is:issue'
    if state != 'all':
        search_query += f' state:{state}'
    if username and repo:
        search_query += f' repo:{username}/{repo}'
    elif username:
        search_query += f' user:{username}'
    
    try:
        resp = s.get(f'{GITHUB_API}/search/issues', params={
            'q': search_query,
            'per_page': 50,
            'sort': 'updated'
        })
        
        if not resp.ok:
            return jsonify({'error': resp.json().get('message', 'Search failed')}), resp.status_code
        
        results = resp.json()
        
        return jsonify({
            'success': True,
            'query': query,
            'total_count': results.get('total_count', 0),
            'items': results.get('items', [])
        })
    
    except Exception as e:
        logging.error(f"Issue search error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/commits', methods=['POST'])
def search_commits():
    """
    Search commits across repositories
    """
    data = request.json or {}
    token = data.get('token')
    query = data.get('query', '').strip()
    username = data.get('username')
    repo = data.get('repo')
    author = data.get('author')
    
    if not query:
        return jsonify({'error': 'Search query required'}), 400
    
    s = github_session(token)
    
    # Build search query
    search_query = query
    if username and repo:
        search_query += f' repo:{username}/{repo}'
    elif username:
        search_query += f' user:{username}'
    if author:
        search_query += f' author:{author}'
    
    try:
        resp = s.get(f'{GITHUB_API}/search/commits', params={
            'q': search_query,
            'per_page': 50,
            'sort': 'committer-date'
        })
        
        if not resp.ok:
            return jsonify({'error': resp.json().get('message', 'Search failed')}), resp.status_code
        
        results = resp.json()
        
        return jsonify({
            'success': True,
            'query': query,
            'total_count': results.get('total_count', 0),
            'items': results.get('items', [])
        })
    
    except Exception as e:
        logging.error(f"Commit search error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/search/all-accounts', methods=['POST'])
def search_all_accounts():
    """
    Search across all signed-in GitHub accounts
    Requires multiple tokens for different accounts
    """
    data = request.json or {}
    accounts = data.get('accounts', [])  # [{'token': 'xxx', 'username': 'user1'}, ...]
    query = data.get('query', '').strip()
    mode = data.get('mode', 'fuzzy')
    search_in = data.get('search_in', ['filename', 'content'])
    
    if not query:
        return jsonify({'error': 'Search query required'}), 400
    
    if not accounts:
        return jsonify({'error': 'No accounts provided'}), 400
    
    all_results = []
    account_summary = []
    
    for account in accounts:
        token = account.get('token')
        username = account.get('username')
        
        if not token:
            continue
        
        try:
            s = github_session(token)
            
            # Get all repos for this account
            resp = s.get(f'{GITHUB_API}/user/repos', params={
                'per_page': 100, 'type': 'all'
            })
            
            if not resp.ok:
                account_summary.append({
                    'username': username,
                    'status': 'failed',
                    'error': 'Failed to fetch repos'
                })
                continue
            
            repos = resp.json()
            account_results = []
            
            # Search each repo
            for repo in repos[:30]:  # Limit per account
                repo_username = repo['owner']['login']
                repo_name = repo['name']
                
                file_results = search_repository_recursive(
                    s, repo_username, repo_name, query, mode, 
                    None, max_depth=4
                )
                
                for result in file_results:
                    result['type'] = 'file'
                    result['username'] = repo_username
                    result['repo'] = repo_name
                    result['account'] = username
                    result['url'] = f'https://github.com/{repo_username}/{repo_name}/blob/main/{result["path"]}'
                    account_results.append(result)
            
            all_results.extend(account_results)
            account_summary.append({
                'username': username,
                'status': 'success',
                'repos_searched': len(repos[:30]),
                'results_found': len(account_results)
            })
        
        except Exception as e:
            logging.error(f"Error searching account {username}: {e}")
            account_summary.append({
                'username': username,
                'status': 'error',
                'error': str(e)
            })
    
    # Sort all results by relevance
    all_results.sort(key=lambda x: x.get('total_score', x.get('score', 0)), reverse=True)
    
    return jsonify({
        'success': True,
        'query': query,
        'mode': mode,
        'accounts_searched': len(accounts),
        'account_summary': account_summary,
        'total_results': len(all_results),
        'results': all_results[:100]  # Limit to top 100
    })


@app.route('/api/search/advanced', methods=['POST'])
def advanced_search():
    """
    Advanced search with complex filters and options
    """
    data = request.json or {}
    token = data.get('token')
    
    # Search parameters
    query = data.get('query', '').strip()
    file_pattern = data.get('file_pattern')  # regex pattern
    exclude_pattern = data.get('exclude_pattern')  # regex pattern
    file_extensions = data.get('file_extensions', [])
    min_size = data.get('min_size', 0)  # bytes
    max_size = data.get('max_size', 1000000)  # bytes
    modified_after = data.get('modified_after')  # ISO date
    language = data.get('language')  # programming language
    
    username = data.get('username')
    repo = data.get('repo')
    
    if not query:
        return jsonify({'error': 'Search query required'}), 400
    
    s = github_session(token)
    
    try:
        # Build advanced search query for GitHub API
        search_parts = [query]
        
        if username and repo:
            search_parts.append(f'repo:{username}/{repo}')
        elif username:
            search_parts.append(f'user:{username}')
        
        if language:
            search_parts.append(f'language:{language}')
        
        if file_pattern:
            search_parts.append(f'filename:{file_pattern}')
        
        if file_extensions:
            ext_queries = ' OR '.join([f'extension:{ext.replace(".", "")}' for ext in file_extensions])
            search_parts.append(f'({ext_queries})')
        
        if min_size > 0:
            search_parts.append(f'size:>={min_size}')
        
        if max_size < 1000000:
            search_parts.append(f'size:<={max_size}')
        
        search_query = ' '.join(search_parts)
        
        # Execute search
        resp = s.get(f'{GITHUB_API}/search/code', params={
            'q': search_query,
            'per_page': 50
        })
        
        if not resp.ok:
            return jsonify({'error': resp.json().get('message', 'Search failed')}), resp.status_code
        
        results = resp.json()
        items = results.get('items', [])
        
        # Apply additional filters
        if exclude_pattern:
            exclude_regex = re.compile(exclude_pattern, re.IGNORECASE)
            items = [item for item in items if not exclude_regex.search(item.get('path', ''))]
        
        return jsonify({
            'success': True,
            'query': search_query,
            'total_count': len(items),
            'items': items,
            'filters_applied': {
                'file_pattern': file_pattern,
                'exclude_pattern': exclude_pattern,
                'file_extensions': file_extensions,
                'language': language,
                'size_range': [min_size, max_size]
            }
        })
    
    except Exception as e:
        logging.error(f"Advanced search error: {e}")
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('DEBUG', 'false').lower() == 'true')
