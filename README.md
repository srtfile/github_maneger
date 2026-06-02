# GitHub Manager Pro 🚀

Advanced web-based GitHub repository management tool with a sleek dark UI.

## Features

- 📁 **Repository Management** — Create, browse, delete repos with stats
- 📄 **File Browser & Editor** — Browse, edit, upload, delete files with syntax highlighting
- ⚡ **GitHub Actions** — View workflows, trigger runs, monitor status
- 🐛 **Issues Tracker** — View, create, and filter issues
- 📜 **Commit History** — Browse recent commits with author info
- 🔐 **Secrets Viewer** — List repository secrets (names only, as per GitHub API)

## Deploy to Render

### Option 1: One-click via render.yaml (recommended)

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New → Blueprint
3. Connect your GitHub repo
4. Render auto-detects `render.yaml` and deploys

### Option 2: Manual Web Service

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Set these values:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60`
5. Add environment variable:
   - `SECRET_KEY` → any long random string
6. Click **Deploy**

## Local Development

```bash
pip install -r requirements.txt
python app.py
```

Open [http://localhost:5000](http://localhost:5000)

## GitHub Token

You need a GitHub Personal Access Token with these scopes:
- `repo` (full repository access)
- `workflow` (GitHub Actions)
- `delete_repo` (if you want to delete repos)

Generate one at: https://github.com/settings/tokens

## Security Note

This app does not store your token — it's used client-side per session only.
For production, consider adding authentication middleware.
