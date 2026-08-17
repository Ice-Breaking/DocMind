import { useState } from 'react';
import { LockOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons';
import { App, Button, Card, Form, Input } from 'antd';
import { login } from '../api';

/** 登录页：对接后端 Gradio /login（form-encoded + access-token cookie） */
export default function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [loading, setLoading] = useState(false);
  const { message } = App.useApp();

  const onFinish = async ({ username, password }: { username: string; password: string }) => {
    setLoading(true);
    try {
      const ok = await login(username, password);
      if (ok) {
        onLoggedIn();
      } else {
        message.error('用户名或密码错误');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="dm-login-bg">
      <Card className="dm-login-card" variant="borderless">
        <div className="dm-login-logo">
          <RobotOutlined />
        </div>
        <h1>DocMind</h1>
        <p className="dm-login-sub">企业知识助理 Agent · 请登录后使用</p>
        <Form onFinish={onFinish} size="large" autoComplete="off">
          <Form.Item name="username" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input prefix={<UserOutlined />} placeholder="用户名" />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
