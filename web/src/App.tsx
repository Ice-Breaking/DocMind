import { lazy, Suspense, useEffect, useState } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { Spin, Modal, Input, Button, Space, message } from 'antd';
import { fetchMe, changePassword, type Me } from './api';
import Login from './pages/Login';
import AppLayout from './components/AppLayout';

// 路由级代码分割：除登录页（首屏直达）外全部懒加载——页面组件按路由
// 自动分包、访问时才拉取，配合 vite manualChunks 的 vendor 分离，
// 首屏 bundle 只含框架 + 登录页，显著减小初始加载体积
const Chat = lazy(() => import('./pages/Chat'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const Admin = lazy(() => import('./pages/Admin'));
const Assistants = lazy(() => import('./pages/Assistants'));
const KnowledgeBases = lazy(() => import('./pages/KnowledgeBases'));
const SessionHistory = lazy(() => import('./pages/SessionHistory'));
const Settings = lazy(() => import('./pages/Settings'));
const Usage = lazy(() => import('./pages/Usage'));
const Badcases = lazy(() => import('./pages/Badcases'));
const Traces = lazy(() => import('./pages/Traces'));
const RetrievalLab = lazy(() => import('./pages/RetrievalLab'));
const Eval = lazy(() => import('./pages/Eval'));
const ApiKeys = lazy(() => import('./pages/ApiKeys'));
const Models = lazy(() => import('./pages/Models'));
const Audit = lazy(() => import('./pages/Audit'));
const Alerts = lazy(() => import('./pages/Alerts'));
const Backups = lazy(() => import('./pages/Backups'));
const Users = lazy(() => import('./pages/Users'));
const Queries = lazy(() => import('./pages/Queries'));

// 懒加载路由首次拉取分包时的过渡 UI（复用全局居中样式）
const PageFallback = (
  <div className="dm-full-center">
    <Spin size="large" />
  </div>
);

/**
 * 路由守卫：应用加载即探 /api/me（Gradio 登录 cookie 同源流转）。
 * 未登录 → /login；登录后回主页。
 * must_change_pwd 为真时弹出不可关闭的强制改密 Modal。
 */
export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const navigate = useNavigate();

  const [showPwdModal, setShowPwdModal] = useState(false);
  const [oldPwd, setOldPwd] = useState('');
  const [newPwd, setNewPwd] = useState('');
  const [confirmPwd, setConfirmPwd] = useState('');
  const [pwdLoading, setPwdLoading] = useState(false);

  const refresh = async () => {
    const m = await fetchMe();
    setMe(m.user ? m : null);
  };

  useEffect(() => {
    refresh().finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (me?.must_change_pwd) {
      setShowPwdModal(true);
    }
  }, [me?.must_change_pwd]);

  const handleChangePassword = async () => {
    if (newPwd !== confirmPwd) {
      message.error('两次输入的新密码不一致');
      return;
    }
    if (newPwd.length < 8) {
      message.error('新密码至少 8 个字符');
      return;
    }
    setPwdLoading(true);
    try {
      await changePassword(oldPwd, newPwd);
      message.success('密码修改成功');
      setShowPwdModal(false);
      // 刷新 me 以清除 must_change_pwd
      const updated = await fetchMe();
      setMe(updated);
    } catch (e: any) {
      message.error(e.message || '修改失败');
    } finally {
      setPwdLoading(false);
    }
  };

  if (!ready) {
    return (
      <div className="dm-full-center">
        <Spin size="large" tip="加载中…" />
      </div>
    );
  }

  return (
    <>
      {/* 路由级 Suspense：懒加载页面分包首次拉取时的过渡 UI */}
      <Suspense fallback={PageFallback}>
      <Routes>
        <Route
          path="/login"
          element={
            me ? <Navigate to="/dashboard" replace /> : (
              <Login
                onLoggedIn={async () => {
                  await refresh();
                  navigate('/dashboard', { replace: true });
                }}
              />
            )
          }
        />
        <Route
          element={me ? <AppLayout me={me} onLogout={() => setMe(null)} /> : <Navigate to="/login" replace />}
        >
          <Route path="/dashboard" element={<Dashboard me={me!} />} />
          <Route path="/chat" element={<Chat me={me!} onLogout={() => setMe(null)} />} />
          <Route path="/assistants" element={<Assistants me={me!} />} />
          <Route path="/kbs" element={<KnowledgeBases />} />
          <Route path="/sessions" element={<SessionHistory me={me!} />} />
          <Route path="/settings" element={<Settings me={me!} onLogout={() => setMe(null)} onRefreshMe={refresh} />} />
          <Route
            path="/usage"
            element={me?.is_admin ? <Usage /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/badcases"
            element={me?.is_admin ? <Badcases /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/traces"
            element={me?.is_admin ? <Traces /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/retrieval-lab"
            element={me?.is_admin ? <RetrievalLab /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/eval"
            element={me?.is_admin ? <Eval /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/api-keys"
            element={me?.is_admin ? <ApiKeys /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/models"
            element={me?.is_admin ? <Models /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/audit"
            element={me?.is_admin ? <Audit /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/alerts"
            element={me?.is_admin ? <Alerts /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/backups"
            element={me?.is_admin ? <Backups /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/users"
            element={me?.is_admin ? <Users me={me} /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/queries"
            element={me?.is_admin ? <Queries /> : <Navigate to="/dashboard" replace />}
          />
          <Route
            path="/admin"
            element={
              me?.is_admin ? (
                <Admin me={me} onLogout={() => setMe(null)} />
              ) : (
                <Navigate to="/dashboard" replace />
              )
            }
          />
          <Route index element={<Navigate to="/dashboard" replace />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
      </Suspense>
      <Modal
        title="首次登录请修改密码"
        open={showPwdModal}
        closable={false}
        maskClosable={false}
        keyboard={false}
        footer={null}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Input.Password placeholder="原密码" value={oldPwd} onChange={e => setOldPwd(e.target.value)} />
          <Input.Password placeholder="新密码（至少8位）" value={newPwd} onChange={e => setNewPwd(e.target.value)} />
          <Input.Password placeholder="确认新密码" value={confirmPwd} onChange={e => setConfirmPwd(e.target.value)} />
          <Button type="primary" block loading={pwdLoading} onClick={handleChangePassword}>
            确认修改
          </Button>
        </Space>
      </Modal>
    </>
  );
}
