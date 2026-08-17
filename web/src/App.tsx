import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { Spin } from 'antd';
import { fetchMe, type Me } from './api';
import Login from './pages/Login';
import Home from './pages/Home';

/**
 * 路由守卫：应用加载即探 /api/me（Gradio 登录 cookie 同源流转）。
 * 未登录 → /login；登录后回主页。
 */
export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const navigate = useNavigate();

  const refresh = async () => {
    const m = await fetchMe();
    setMe(m.user ? m : null);
  };

  useEffect(() => {
    refresh().finally(() => setReady(true));
  }, []);

  if (!ready) {
    return (
      <div className="dm-full-center">
        <Spin size="large" tip="加载中…" />
      </div>
    );
  }

  return (
    <Routes>
      <Route
        path="/login"
        element={
          me ? <Navigate to="/" replace /> : (
            <Login
              onLoggedIn={async () => {
                await refresh();
                navigate('/', { replace: true });
              }}
            />
          )
        }
      />
      <Route
        path="/"
        element={me ? <Home me={me} onLogout={() => setMe(null)} /> : <Navigate to="/login" replace />}
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
