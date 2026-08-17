import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  App,
  Button,
  Card,
  Descriptions,
  Divider,
  Input,
  Modal,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  DeleteOutlined,
  DownloadOutlined,
  ExportOutlined,
  LockOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { deleteAccount, type Me } from '../api';

const { Title, Text, Paragraph } = Typography;

/**
 * 个人设置：账号信息 / 数据导出 / 注销账号。
 * 修改密码已独立为顶部用户菜单的弹窗入口，本页不再重复。
 */
export default function Settings({ me, onLogout }: { me: Me; onLogout: () => void }) {
  const { message: msgApi } = App.useApp();
  const navigate = useNavigate();

  /* ---- 注销账号 ---- */
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [deleting, setDeleting] = useState(false);

  const openDeleteConfirm = () => {
    setConfirmText('');
    setConfirmOpen(true);
  };

  const handleDeleteAccount = async () => {
    setDeleting(true);
    try {
      await deleteAccount();
      msgApi.success('账号已注销');
      setConfirmOpen(false);
      onLogout();
      navigate('/login', { replace: true });
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '注销失败');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div style={{ padding: '24px 32px', maxWidth: 760, margin: '0 auto' }}>
      <Title level={3} style={{ marginBottom: 8 }}>个人设置</Title>
      <Text type="secondary">账号信息、个人数据与账号安全</Text>

      <Divider />

      {/* ---- 1. 账号信息 ---- */}
      <Card>
        <Space align="center" style={{ marginBottom: 16 }}>
          <UserOutlined style={{ fontSize: 18 }} />
          <Title level={5} style={{ margin: 0 }}>账号信息</Title>
        </Space>
        <Descriptions column={1} size="small">
          <Descriptions.Item label="用户名">{me.user}</Descriptions.Item>
          <Descriptions.Item label="角色">
            {me.is_admin ? <Tag color="gold">管理员</Tag> : <Tag>普通用户</Tag>}
          </Descriptions.Item>
        </Descriptions>
        <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
          <LockOutlined /> 修改密码请点击右上角（侧边栏顶部）用户菜单中的「修改密码」。
        </Paragraph>
      </Card>

      <Divider />

      {/* ---- 2. 导出数据 ---- */}
      <Card>
        <Space align="center" style={{ marginBottom: 12 }}>
          <ExportOutlined style={{ fontSize: 18 }} />
          <Title level={5} style={{ margin: 0 }}>导出数据</Title>
        </Space>
        <Paragraph type="secondary" style={{ marginBottom: 16 }}>
          根据 GDPR 数据可携带权要求，你可以导出本账号的全部个人数据，
          包括会话记录、消息内容与反馈信息。导出文件为归档格式，下载后即可离线保存。
        </Paragraph>
        <Button icon={<DownloadOutlined />} href="/api/me/export" download="my-data-export">
          下载我的数据
        </Button>
      </Card>

      <Divider />

      {/* ---- 3. 注销账号 ---- */}
      <Card style={{ borderColor: '#ffccc7' }}>
        <Space align="center" style={{ marginBottom: 12 }}>
          <DeleteOutlined style={{ fontSize: 18, color: '#cf1322' }} />
          <Title level={5} style={{ margin: 0, color: '#cf1322' }}>注销账号</Title>
        </Space>
        <Paragraph type="secondary" style={{ marginBottom: 16 }}>
          注销后将永久删除你的账号及所有关联数据（会话、消息、反馈等），
          该操作不可恢复，请谨慎操作。
        </Paragraph>
        <Button danger icon={<DeleteOutlined />} onClick={openDeleteConfirm}>
          注销我的账号
        </Button>
      </Card>

      {/* ---- 注销确认 Modal ---- */}
      <Modal
        title="确认注销账号"
        open={confirmOpen}
        onOk={handleDeleteAccount}
        onCancel={() => setConfirmOpen(false)}
        okText="永久注销"
        okButtonProps={{ danger: true, disabled: confirmText !== me.user }}
        confirmLoading={deleting}
        cancelText="取消"
        maskClosable={false}
      >
        <Paragraph>
          此操作将永久删除账号 <Text strong>{me.user}</Text> 的全部数据，且不可恢复。
        </Paragraph>
        <Paragraph>
          请输入你的用户名 <Text code>{me.user}</Text> 以确认注销：
        </Paragraph>
        <Input
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          placeholder={me.user}
          autoFocus
        />
      </Modal>
    </div>
  );
}
