// This file is also exercised by the extension's Node tests.
function dashboardLink(base, transcriptId) {
  const url = new URL(base.trim());
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('Use an http:// or https:// dashboard URL without credentials.');
  }
  url.pathname = transcriptId ? `/meetings/${encodeURIComponent(transcriptId)}` : '/';
  // Never carry query-string credentials into dashboard navigation.
  url.search = '';
  url.hash = '';
  return url.href;
}
