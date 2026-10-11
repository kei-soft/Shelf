export async function api(path, options = {}) {
  const response = await fetch('/api' + path, options);
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(typeof result.detail === 'string' ? result.detail : '요청을 처리하지 못했습니다.');
  }
  return response.json();
}
export const post = body => ({method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
