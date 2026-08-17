import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { Spin, Modal, Input, Button, Space, message } from 'antd';
import { fetchMe, changePassword, type Me } from './api';
import Login from './pages/Login';
import Chat from './pages/Chat';
import Admin from './pages/Admin';
import Dashboard from './pages/Dashboard';
import Assistants from './pages/Assistants';
import KnowledgeBases from './pages/KnowledgeBases';
import SessionHistory from './pages/SessionHistory';
import Settings from './pages/Settings';
import Usage from './pages/Usage';
import Badcases from './pages/Badcases';
import Traces from './pages/Traces';
import RetrievalLab from './pages/RetrievalLab';
import Eval from './pages/Eval';
import ApiKeys from './pages/ApiKeys';
import Models from './pages/Models';
import Audit from './pages/Audit';
import Alerts from './pages/Alerts';
import Backups from './pages/Backups';
import Users from './pages/Users';
import Queries from './pages/Queries';
import AppLayout from './components/AppLayout';

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
          <Route path="/settings" element={<Settings me={me!} onLogout={() => setMe(null)} />} />
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
