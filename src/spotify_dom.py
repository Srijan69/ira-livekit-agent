"""Small observable Spotify DOM operations, also exercised with browser fixtures."""

SELECT_TRACK = r"""([requestedTitle, requestedArtist]) => {
const norm = s => (s || '').normalize('NFKC').replace(/\s+/g, ' ').trim().toLowerCase();
const title = norm(requestedTitle), artist = norm(requestedArtist);
const rows = [...document.querySelectorAll('[data-testid="tracklist-row"]')];
const matches = [];
for (const [row_index, row] of rows.entries()) {
    const link = row.querySelector('a[data-testid="internal-track-link"], a[href*="/track/"]');
    if (!link || norm(link.innerText || link.textContent) !== title) continue;
    const artists = [...row.querySelectorAll('a[href*="/artist/"]')].map(a => (a.innerText || a.textContent).trim());
    if (artist && !artists.some(a => norm(a) === artist)) continue;
    const track_path = new URL(link.href, location.href).pathname;
    if (!matches.some(m => m.track_path === track_path)) matches.push({row, row_index, title: (link.innerText || link.textContent).trim(), artists, track_path});
}
if (!matches.length) return {status: 'not-found'};
// Spotify often indexes a single and its album release separately. Identical
// titles with the same observed artist set satisfy a song request; different
// artists (or unknown artist identity) still need clarification.
const identities = new Set(matches.map(m => JSON.stringify(m.artists.map(norm).sort())));
if (matches.length > 1 && (identities.size > 1 || matches.some(m => !m.artists.length)))
    return {status: 'ambiguous', candidates: matches.map(m => ({title:m.title, artists:m.artists}))};
const m = matches[0];
const play = [...m.row.querySelectorAll('button')].find(b => /^play(?:\s|$)/i.test(b.getAttribute('aria-label') || ''));
const pause = [...m.row.querySelectorAll('button')].find(b => /^pause(?:\s|$)/i.test(b.getAttribute('aria-label') || ''));
if (play) return {status:'ready', title:m.title, artists:m.artists, track_path:m.track_path, row_index:m.row_index};
else if (!pause) return {status: 'control-unavailable'};
return {status:'selected', title:m.title, artists:m.artists, track_path:m.track_path};
}
"""

PLAYER_STATE = r"""() => {
const widget = document.querySelector('[data-testid="now-playing-widget"], [data-testid="now-playing-widget-v2"]');
const footer = document.querySelector('[data-testid="now-playing-bar"], footer');
const root = widget || footer;
const link = root && root.querySelector('a[data-testid="context-item-link"], a[href*="/track/"]');
const titleNode = root && root.querySelector('[data-testid="context-item-info-title"]');
const trackLink = root && [...root.querySelectorAll('a[href*="/track/"]')][0];
const audio = document.querySelector('audio');
const pp = document.querySelector('[data-testid="control-button-playpause"]');
const label = pp && (pp.getAttribute('aria-label') || '');
const enabled = pp && !pp.disabled && pp.getAttribute('aria-disabled') !== 'true';
const available = !!(audio || (enabled && /^(play|pause)(?:\s|$)/i.test(label)));
const playing = audio ? !audio.paused && !audio.ended && audio.currentTime > 0 : !!(enabled && /^pause(?:\s|$)/i.test(label));
const identity = trackLink || (link && /\/track\//.test(link.href) ? link : null);
const positionText = document.querySelector('[data-testid="playback-position"]')?.innerText?.trim() || '';
const position_seconds = audio ? audio.currentTime : (/^\d+(?::\d+){1,2}$/.test(positionText) ? positionText.split(':').reduce((total,part)=>total*60+Number(part),0) : null);
return {
    available, playing,
    position_seconds,
    title: ((titleNode && (titleNode.innerText || titleNode.textContent)) || (link && (link.innerText || link.textContent)) || (identity && (identity.innerText || identity.textContent)) || '').trim(),
    artists: root ? [...root.querySelectorAll('a[href*="/artist/"]')].map(a => (a.innerText || a.textContent).trim()) : [],
    track_path: identity ? new URL(identity.href, location.href).pathname : ''
};
}
"""
