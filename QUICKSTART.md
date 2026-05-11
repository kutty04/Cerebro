# CodeRAG Setup Checklist ✅

## Phase 1: Setup (30 mins)

### Supabase Setup
- [ ] Create Supabase project at supabase.com
- [ ] Go to SQL Editor
- [ ] Copy and run ALL SQL from `supabase_setup.sql`
- [ ] Verify table created: check "Tables" in sidebar
- [ ] Copy Project URL (Settings → API)
- [ ] Copy Anon Key (Settings → API)

### Hugging Face
- [ ] Create HF account at huggingface.co
- [ ] Go to Settings → Access Tokens
- [ ] Create new token (read-only)
- [ ] Copy token value

### Local Repos
- [ ] Create folder: `mkdir ~/Desktop/coderag-data`
- [ ] Copy/clone your repos into it
- [ ] Verify structure:
  ```bash
  ls ~/Desktop/coderag-data/
  # Should see your repo folders
  ```

---

## Phase 2: Backend (20 mins)

### Install & Configure
```bash
# Clone files (or copy manually)
pip install -r requirements.txt

# Create .env
cp .env.example .env

# Edit .env with your values
nano .env
# SUPABASE_URL=your_url
# SUPABASE_KEY=your_key
# HF_TOKEN=your_token
# REPOS_PATH=~/Desktop/coderag-data
```

### Index Your Code
```bash
python indexer.py
```

Wait for completion. You should see:
```
✅ Successfully indexed: XXX snippets
```

Check Supabase:
```sql
SELECT COUNT(*) FROM code_snippets;
-- Should show: count = XXX (same as indexer output)
```

### Test Backend
```bash
python app.py
```

In another terminal:
```bash
curl http://localhost:8000/health
# Should return JSON with status: "ok"
```

---

## Phase 3: Frontend (15 mins)

### Create React App
```bash
npm create vite@latest coderag-frontend -- --template react
cd coderag-frontend
npm install
npm install lucide-react
```

### Add Files
```bash
# Copy CodeRAG.jsx to src/components/CodeRAG.jsx
# Copy CodeRAG.css to src/components/CodeRAG.css
```

### Update App
Edit `src/App.jsx`:
```jsx
import CodeRAG from './components/CodeRAG'

export default function App() {
  return <CodeRAG />
}
```

### Create .env
```
VITE_API_URL=http://localhost:8000
```

### Run
```bash
npm run dev
# Open http://localhost:5173
```

---

## Phase 4: Test (5 mins)

### Checklist
- [ ] Backend running at `http://localhost:8000`
- [ ] Frontend running at `http://localhost:5173`
- [ ] Can type in search box
- [ ] Can click "Search"
- [ ] Gets results in 5-10 seconds
- [ ] Results show code snippets
- [ ] Can copy snippets
- [ ] Dark mode looks good

### Test Queries
- "How do I handle errors?"
- "Show me database queries"
- "Authentication logic"
- Your code-specific question

---

## Phase 5: Deploy (30 mins each)

### Backend → HF Spaces
```bash
# Create Space at huggingface.co/spaces
# Template: Python/FastAPI
# Upload files:
# - app.py
# - requirements.txt
# - .env (with secrets)
```

Get the Space URL: `https://your-username-coderag.hf.space`

### Frontend → Vercel
```bash
npm run build

# Then:
# 1. Push to GitHub
# 2. Connect to vercel.com
# 3. Add env var: VITE_API_URL=your_hf_space_url
# 4. Deploy
```

---

## 🚨 Troubleshooting

| Problem | Solution |
|---------|----------|
| "No snippets found" | Run `python indexer.py` again, check `REPOS_PATH` |
| "Failed to connect Supabase" | Check `.env` credentials, verify URL format |
| "Invalid HF token" | Regenerate at huggingface.co/settings/tokens |
| "Slow searches" | Reduce `top_k` value, check Supabase index |
| "404 on frontend" | Check `VITE_API_URL` points to running backend |

---

## 📊 Verification Commands

```bash
# Check indexer output
ls -lh ~/Desktop/coderag-data/

# Check Supabase connection
python -c "import supabase; print('✅ Supabase OK')"

# Check HF token
curl -H "Authorization: Bearer $HF_TOKEN" https://api-inference.huggingface.co/status

# Count indexed snippets
# In Supabase SQL Editor:
SELECT COUNT(*) FROM code_snippets;

# Test search endpoint
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "top_k": 3}'
```

---

## 📈 Next: LinkedIn Post Template

```
Built CodeRAG — semantic search for my codebase

Instead of grep-ing 1000 files, I ask it naturally:
"How do I handle GPS?"
→ Returns relevant snippets + AI explanation

Tech: pgvector + Supabase + HF Inference
Indexed 5 repos, works on free tier

[Demo Link] [GitHub] [Try It]

#buildingpublic #llm #vectordatabase
```

---

## ✨ You're Done!

You now have a fully working RAG system. Next:
1. Add more repos
2. Deploy live
3. Share with friends
4. Post on LinkedIn
5. Scale to side income

Let's gooo! 🚀
