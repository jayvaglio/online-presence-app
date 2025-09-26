# People Presence App — Debugged & Fixed

This repository contains a ready-to-deploy full-stack project that helps evaluate a person's online presence/brand quality. **This version fixes a common runtime/build error** (`SyntaxError: /index.tsx: Unexpected token (1:0)`) by removing accidental TypeScript assumptions, adding robust Vite config (React plugin), and adding safety checks and backend tests.

### High-level changes made to fix the error

- Ensured the frontend uses **.jsx** files (no `.tsx` files in the frontend). If you previously copied markdown or README contents into a file named `index.tsx`, that will cause the error you saw — this doc now includes a small safety script to detect and fail fast if `.tsx` files exist unexpectedly.
- Added a `vite.config.js` which explicitly uses `@vitejs/plugin-react` and a proxy for `/api` to the backend for `npm run dev` convenience.
- Hardened the backend to export the `app` object so automated tests can run (and only listen when executed directly). Added Mocha + Supertest tests for the backend `POST /search` endpoint.
- Added a `frontend/scripts/check-no-tsx.js` script that runs before `vite` in `dev` to detect accidental `.tsx` files and give a clear error message.

---

## File tree (updated)

```
people-presence-app/
├─ frontend/
│  ├─ package.json
│  ├─ vite.config.js
│  ├─ index.html
│  ├─ src/
│  │  ├─ main.jsx
│  │  ├─ App.jsx
│  │  ├─ components/SearchForm.jsx
│  │  └─ styles.css
│  └─ scripts/
│     └─ check-no-tsx.js
├─ backend/
│  ├─ package.json
│  ├─ index.js
│  └─ tests/
│     └─ search.test.js
├─ streamlit_app.py
├─ .gitignore
└─ README.md
```

---

# --- BEGIN FILES ---

## .gitignore

```
node_modules/
dist/
.env
.vscode/
.idea/
.DS_Store
```

---

## frontend/package.json

```json
{
  "name": "presence-frontend",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "dev": "node ./scripts/check-no-tsx.js && vite",
    "build": "vite build",
    "preview": "vite preview --port 5173"
  },
  "dependencies": {
    "axios": "^1.4.0",
    "react": "^18.2.0",
    "react-dom": "^18.2.0"
  },
  "devDependencies": {
    "vite": "^5.0.0",
    "@vitejs/plugin-react": "^4.0.0"
  }
}
```

Notes:
- `dev` runs a small file-check script before starting Vite to help catch the exact issue that produced `SyntaxError: /index.tsx: Unexpected token (1:0)`.

---

## frontend/vite.config.js

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // During local dev, calls to /api will be proxied to your backend.
      '/api': 'http://localhost:3000'
    }
  }
})
```

This ensures Vite loads React JSX correctly and provides a convenient dev-time proxy to the backend.

---

## frontend/scripts/check-no-tsx.js

```js
// Small helper that scans the frontend folder for .tsx files and exits non-zero
// with a clear message so developers don't accidentally leave/introduce .tsx files
// when the project is set up for plain JavaScript/JSX.

const fs = require('fs')
const path = require('path')

function findTsx(dir){
  const out = []
  const items = fs.readdirSync(dir, { withFileTypes: true })
  for(const it of items){
    const p = path.join(dir, it.name)
    if(it.isDirectory()){
      out.push(...findTsx(p))
    } else {
      if(p.endsWith('.tsx')) out.push(p)
    }
  }
  return out
}

const root = path.resolve(__dirname, '..')
const tsxFiles = findTsx(root)
if(tsxFiles.length){
  console.error('\nERROR: Found .tsx files in the frontend directory. This project uses plain JavaScript/JSX.\n')
  console.error('Files:')
  tsxFiles.forEach(f => console.error(' -', f))
  console.error('\nSolution:')
  console.error(' - If you intended TypeScript, rename files appropriately and add TypeScript tooling, or')
  console.error(' - Rename files to .jsx/.js and ensure they contain valid JSX/JS code.\n')
  process.exit(1)
}
process.exit(0)
```

---

## frontend/index.html

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>People Presence — Brand Quality</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

---

## frontend/src/main.jsx

```jsx
import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles.css'

const rootEl = document.getElementById('root')
if(!rootEl) throw new Error('Root element not found: please ensure index.html contains <div id="root"></div>')

createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

---

## frontend/src/App.jsx

```jsx
import React, {useState} from 'react'
import SearchForm from './components/SearchForm'

export default function App(){
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  return (
    <div className="app">
      <header className="header">
        <h1>People Presence — Brand Quality</h1>
        <p className="subtitle">Search web for a person's name and surface presence, sentiment, and quick metrics.</p>
      </header>

      <main>
        <SearchForm
          onSearchStart={() => { setLoading(true); setError(null); setResults(null); }}
          onResults={(r)=>{ setResults(r); setLoading(false); }}
          onError={(e)=>{ setError(e); setLoading(false); }}
        />

        {loading && <div className="box">Searching... (querying backend)</div>}
        {error && <div className="box error">Error: {error}</div>}

        {results && (
          <section className="box results">
            <h2>Summary</h2>
            <div className="grid">
              <div><b>Name:</b> {results.query}</div>
              <div><b>Top domain:</b> {results.topDomain || '—'}</div>
              <div><b>Estimated presence score:</b> {results.presenceScore}</div>
              <div><b>General sentiment:</b> {results.sentiment}</div>
            </div>

            <h3>Top items</h3>
            <ul>
              {results.items && results.items.map((it, i) => (
                <li key={i}>
                  <a href={it.link} target="_blank" rel="noreferrer">{it.title}</a>
                  <div className="meta">{it.snippet}</div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>

      <footer className="footer">Made to deploy on Railway (backend) + Streamlit (frontend wrapper). Set BACKEND_URL env var when deploying Streamlit.</footer>
    </div>
  )
}
```

---

## frontend/src/components/SearchForm.jsx

```jsx
import React, {useState} from 'react'
import axios from 'axios'

export default function SearchForm({onSearchStart, onResults, onError}){
  const [name, setName] = useState('')
  const [backendUrl, setBackendUrl] = useState(process.env.REACT_APP_BACKEND_URL || '')

  const submit = async (e) =>{
    e.preventDefault()
    if(!name.trim()) return onError('Please enter a name')
    onSearchStart()
    try{
      const url = (backendUrl || window.__BACKEND_URL__ || '/api') + '/search'
      const res = await axios.post(url, { name })
      onResults(res.data)
    }catch(err){
      console.error(err)
      onError(err?.response?.data?.error || err.message || 'Unknown error')
    }
  }

  return (
    <form className="box form" onSubmit={submit}>
      <label>
        Person's name
        <input value={name} onChange={e=>setName(e.target.value)} placeholder="e.g. Jane Doe" />
      </label>
      <small>Optionally set backend URL in the field below (useful in Streamlit):</small>
      <input value={backendUrl} onChange={e=>setBackendUrl(e.target.value)} placeholder="https://your-backend.onrailway.app" />
      <div className="row">
        <button type="submit">Search Presence</button>
      </div>
    </form>
  )
}
```

---

## frontend/src/styles.css

```css
body{ font-family: Inter, system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial; margin:0; background:#f6f7fb; color:#111 }
.app{ max-width:1000px; margin:28px auto; padding:12px }
.header h1{ margin:0 }
.header .subtitle{ color:#555 }
.box{ background:#fff; padding:16px; border-radius:12px; box-shadow:0 6px 18px rgba(20,20,40,0.04); margin-top:16px }
.form input{ display:block; width:100%; margin-top:8px; padding:10px; border-radius:8px; border:1px solid #e6e9ef }
.row{ margin-top:12px }
button{ padding:10px 16px; border-radius:10px; border:0; background:#1f6feb; color:white }
.results .grid{ display:grid; grid-template-columns:repeat(2,1fr); gap:8px }
.meta{ color:#666; font-size:0.9rem }
.error{ border-left:4px solid #ff5a5a }
.footer{ margin-top:22px; color:#666; font-size:0.85rem }
```

---

## backend/package.json

```json
{
  "name": "presence-backend",
  "version": "1.0.0",
  "main": "index.js",
  "scripts": {
    "start": "node index.js",
    "dev": "nodemon index.js",
    "test": "mocha --exit"
  },
  "dependencies": {
    "axios": "^1.4.0",
    "express": "^4.18.2",
    "cors": "^2.8.5"
  },
  "devDependencies": {
    "mocha": "^10.2.0",
    "supertest": "^6.3.3"
  }
}
```

Added `test` script and test deps to run the backend unit tests.

---

## backend/index.js

```js
// Simple backend that queries SerpAPI (if SERPAPI_KEY set) or returns dummy results.
const express = require('express')
const axios = require('axios')
const cors = require('cors')
const app = express()
app.use(cors())
app.use(express.json())

const PORT = process.env.PORT || 3000

function scorePresence(items){
  // quick heuristic: more unique domains & presence -> higher score
  const domains = new Set(items.map(i=>{
    try{ return new URL(i.link).hostname.replace(/^www\./i,'').toLowerCase() }catch(e){ return 'unknown' }
  }))
  const score = Math.min(100, Math.round((domains.size * 12) + Math.min(items.length, 10) * 3))
  return score
}

function estimateSentiment(items){
  // naive: look for positive/negative words in snippet
  const text = items.map(i=> (i.snippet||'') ).join(' ').toLowerCase()
  const plus = ['award','honor','lead','founder','ceo','win','positive','celebrat']
  const minus = ['scandal','charged','arrest','lawsuit','controvers', 'accused']
  const p = plus.reduce((s,w)=> s + (text.includes(w)?1:0), 0)
  const m = minus.reduce((s,w)=> s + (text.includes(w)?1:0), 0)
  if(p>m) return 'positive'
  if(m>p) return 'negative'
  return 'mixed/neutral'
}

app.post('/search', async (req, res)=>{
  const { name } = req.body || {}
  if(!name) return res.status(400).json({ error: 'name required' })

  // If SERPAPI_KEY provided, call SerpAPI
  if(process.env.SERPAPI_KEY && process.env.SERPAPI_KEY !== ''){
    try{
      const params = new URLSearchParams({ q: name, engine: 'google', api_key: process.env.SERPAPI_KEY, num: '10' })
      const url = `https://serpapi.com/search?${params.toString()}`
      const r = await axios.get(url)
      const serp = r.data.organic_results || []
      const items = serp.map(o=>({ title: o.title || o.snippet || name, link: o.link || o.source || '#', snippet: o.snippet || '' }))
      const presenceScore = scorePresence(items)
      const sentiment = estimateSentiment(items)
      const topDomain = items[0] ? (()=>{ try{return (new URL(items[0].link)).hostname.replace(/^www\./,'')}catch(e){return null}})() : null
      return res.json({ query: name, items, presenceScore, sentiment, topDomain })
    }catch(err){
      console.error('SerpAPI error', err?.response?.data || err.message)
      return res.status(500).json({ error: 'search provider error' })
    }
  }

  // Fallback: dummy sample results (useful for dev without an API key)
  const items = [
    { title: `${name} — LinkedIn`, link: `https://www.linkedin.com/search/results/all/?keywords=${encodeURIComponent(name)}`, snippet: 'Professional profile — role, experiences' },
    { title: `${name} — Twitter`, link: `https://twitter.com/search?q=${encodeURIComponent(name)}`, snippet: 'Recent tweets and engagement' },
    { title: `${name} — Medium`, link: `https://medium.com/search?q=${encodeURIComponent(name)}`, snippet: 'Articles & writing samples' },
    { title: `${name} — Personal Website`, link: `https://www.google.com/search?q=${encodeURIComponent(name+" personal website")}`, snippet: 'Personal website or portfolio (if available)' }
  ]
  const presenceScore = scorePresence(items)
  const sentiment = estimateSentiment(items)
  const topDomain = 'linkedin.com'
  res.json({ query: name, items, presenceScore, sentiment, topDomain })
})

app.get('/', (req,res)=> res.send('People Presence Backend — healthy'))

// Only start listening if this file is run directly. Export the app for tests.
if(require.main === module){
  app.listen(PORT, ()=> console.log('Listening on', PORT))
}

module.exports = app
```

Notes:
- The `require.main === module` guard allows `supertest` to import the app without the server auto-listening.

---

## backend/tests/search.test.js

```js
const request = require('supertest')
const app = require('../index')
const assert = require('assert')

describe('Backend /search endpoint', function(){
  it('returns 400 when name missing', async function(){
    await request(app).post('/search').send({}).expect(400)
  })

  it('returns valid structure for a provided name', async function(){
    const res = await request(app).post('/search').send({ name: 'Alice Example' }).expect(200)
    assert.strictEqual(res.body.query, 'Alice Example')
    assert.ok(Array.isArray(res.body.items), 'items should be an array')
    assert.strictEqual(typeof res.body.presenceScore, 'number')
    assert.ok(['positive','negative','mixed/neutral'].includes(res.body.sentiment))
  })
})
```

Run these tests locally with:

```bash
cd backend
npm install
npm test
```

---

## streamlit_app.py

```py
# Minimal Streamlit wrapper that calls the backend API and displays results inside Streamlit.
import os
import requests
import streamlit as st

st.set_page_config(page_title='People Presence', layout='wide')
st.title('People Presence — Brand Quality')

BACKEND = os.environ.get('BACKEND_URL', '').rstrip('/')
if not BACKEND:
    st.warning('BACKEND_URL environment variable is not set. Set it to your deployed backend (Railway) for full functionality.')

name = st.text_input("Person's name", '')
if st.button('Search'):
    if not name.strip():
        st.error('Please enter a name')
    else:
        if not BACKEND:
            st.info('Showing a demo offline result...')
            st.write({
                'query': name,
                'presenceScore': 34,
                'sentiment': 'mixed/neutral',
                'items': [
                    {'title': f'{name} — LinkedIn', 'link': 'https://www.linkedin.com', 'snippet': 'Professional profile'},
                    {'title': f'{name} — Twitter', 'link': 'https://twitter.com', 'snippet': 'Tweets & mentions'},
                ]
            })
        else:
            try:
                resp = requests.post(f'{BACKEND}/search', json={'name': name}, timeout=25)
                resp.raise_for_status()
                data = resp.json()
                st.subheader('Summary')
                st.write('Presence score:', data.get('presenceScore'))
                st.write('Sentiment:', data.get('sentiment'))
                st.subheader('Top items')
                for it in data.get('items',[]):
                    st.markdown(f"- [{it.get('title')}]({it.get('link')})  \\n  {it.get('snippet')}")
            except Exception as e:
                st.error('Error calling backend: ' + str(e))
```

---

## README.md (updated troubleshooting)

```md
# People Presence — Brand Quality

This repo contains a frontend (React), backend (Node/Express) and a Streamlit wrapper.

## Quick start (local)

### Backend

```bash
cd backend
npm install
# optionally set SERPAPI_KEY in your environment
node index.js
```

Run tests:

```bash
npm test
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# open http://localhost:5173
```

### Streamlit (optional)

Set `BACKEND_URL` env var to your backend (e.g. http://localhost:3000) and run:

```bash
pip install streamlit requests
streamlit run streamlit_app.py
```

## Deploying

### Railway (backend)
1. Create a new Railway project and link this repo or upload the `backend/` folder.
2. Set environment variable `SERPAPI_KEY` if you have one, or leave empty to use the demo fallback.
3. Deploy; Railway will give you a public URL (e.g. https://my-backend.up.railway.app).

### Streamlit (frontend wrapper)
1. Connect Streamlit Community Cloud to this GitHub repository.
2. In the app settings, set `BACKEND_URL` to your Railway backend URL.
3. Deploy. Streamlit will run the `streamlit_app.py` and present the UI.

## If you saw `SyntaxError: /index.tsx: Unexpected token (1:0)`

This error commonly happens when a file named `index.tsx` (or another `.tsx` file) contains non-TSX content (for example, Markdown or a README) or when the project is configured as plain JavaScript but a `.tsx` file exists and the build tooling doesn't expect it.

To fix:

1. **Check for stray files**: Ensure you didn't accidentally paste markdown into `index.tsx` (or create an `index.tsx` file at the repository root). Remove/rename that file.
2. **Use .jsx instead of .tsx** if you're not using TypeScript. The frontend in this repo uses `.jsx` files.
3. **If you want TypeScript**, add TypeScript tooling and rename files properly (e.g. `npm install -D typescript @types/react @types/react-dom` and update Vite config to support TypeScript). This repo currently uses plain JavaScript + JSX.
4. **Run the included check**: `npm run dev` for the frontend will run `frontend/scripts/check-no-tsx.js` and give a clear error explaining the location of any `.tsx` files.

If you're still stuck, tell me:
- The exact command you ran (for example: `cd frontend && npm run dev` or `npm start`)
- The full error stack
- Whether you intentionally wanted TypeScript support

I'll walk you through the exact fix.
```

---

# --- END FILES ---

