(() => {
  const TOKEN_KEY = "argusv_access_token";
  const REFRESH_KEY = "argusv_refresh_token";

  function getAccessToken() {
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  function setTokens(accessToken, refreshToken) {
    if (accessToken) localStorage.setItem(TOKEN_KEY, accessToken);
    else localStorage.removeItem(TOKEN_KEY);
    if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken);
    else localStorage.removeItem(REFRESH_KEY);
  }

  function clearTokens() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
  }

  function authHeaders(headers = {}) {
    const token = getAccessToken();
    const out = { ...headers };
    if (token) out.Authorization = `Bearer ${token}`;
    return out;
  }

  async function authFetch(url, options = {}) {
    const next = { ...options, headers: authHeaders(options.headers || {}) };
    return fetch(url, next);
  }

  async function fetchMe() {
    const token = getAccessToken();
    if (!token) return null;
    const res = await authFetch("/auth/me");
    if (!res.ok) return null;
    return res.json();
  }

  function logout() {
    clearTokens();
    window.location.href = "/static/login.html";
  }

  async function requireAuth() {
    const me = await fetchMe();
    if (!me) {
      clearTokens();
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/static/login.html?next=${next}`;
      return null;
    }
    return me;
  }

  window.ArgusAuth = {
    getAccessToken,
    setTokens,
    clearTokens,
    authHeaders,
    authFetch,
    fetchMe,
    requireAuth,
    logout,
  };
})();

