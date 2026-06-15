const https = require('https');

const GITHUB_TOKEN = process.env.GH_TOKEN;
const GITHUB_REPO = process.env.GH_REPO;

function githubRequest(method, path, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const options = {
      hostname: 'api.github.com',
      path,
      method,
      headers: {
        'Authorization': `token ${GITHUB_TOKEN}`,
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'paris-bot',
        'Content-Type': 'application/json',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    };
    const req = https.request(options, res => {
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(body) }));
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

async function getFile(filename) {
  const r = await githubRequest('GET', `/repos/${GITHUB_REPO}/contents/${filename}`);
  if (r.status === 404) return { content: null, sha: null };
  const content = JSON.parse(Buffer.from(r.data.content, 'base64').toString());
  return { content, sha: r.data.sha };
}

async function saveFile(filename, content, sha, message) {
  const encoded = Buffer.from(JSON.stringify(content, null, 2)).toString('base64');
  const body = { message, content: encoded };
  if (sha) body.sha = sha;
  return githubRequest('PUT', `/repos/${GITHUB_REPO}/contents/${filename}`, body);
}

exports.handler = async (event) => {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
  };

  if (event.httpMethod === 'OPTIONS') return { statusCode: 200, headers, body: '' };
  if (event.httpMethod !== 'POST') return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' }) };

  try {
    const { pari, resultat } = JSON.parse(event.body);
    if (!pari || !resultat) throw new Error('Données manquantes');

    // 1. Charger historique existant
    const { content: histo, sha: hstoSha } = await getFile('historique.json');
    const historique = histo || { paris: [] };

    // 2. Ajouter le résultat
    const entry = {
      ...pari,
      resultat,
      date_resultat: new Date().toISOString(),
      gain_tnd: resultat === 'gagné' ? parseFloat(((pari.mise_tnd || 0) * (pari.cote || 1) - (pari.mise_tnd || 0)).toFixed(2)) : -(pari.mise_tnd || 0),
    };
    historique.paris.unshift(entry);

    // Garde les 200 derniers
    if (historique.paris.length > 200) historique.paris = historique.paris.slice(0, 200);

    // Stats globales
    const total = historique.paris.length;
    const gagnes = historique.paris.filter(p => p.resultat === 'gagné').length;
    historique.stats = {
      total,
      gagnes,
      perdus: total - gagnes,
      taux: total ? Math.round(gagnes / total * 100) : 0,
      profit_net: parseFloat(historique.paris.reduce((s, p) => s + (p.gain_tnd || 0), 0).toFixed(2)),
      derniere_maj: new Date().toISOString(),
    };

    // 3. Sauvegarder
    await saveFile('historique.json', historique, hstoSha, `📊 Résultat : ${pari.match} — ${resultat}`);

    return { statusCode: 200, headers, body: JSON.stringify({ success: true, stats: historique.stats }) };
  } catch (e) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: e.message }) };
  }
};
