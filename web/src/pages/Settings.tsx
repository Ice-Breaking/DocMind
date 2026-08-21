import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  App,
  Button,
  Segmented,
  Card,
  Descriptions,
  Divider,
  Input,
  Modal,
  Space,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  DeleteOutlined,
  DownloadOutlined,
  ExportOutlined,
  LockOutlined,
  UploadOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { changeAvatar, deleteAccount, uploadAvatar, type Me } from '../api';
import { compressImageToAvatar } from '../img';
import AvatarPicker from '../components/AvatarPicker';
import UserAvatar from '../components/UserAvatar';

const { Title, Text, Paragraph } = Typography;

/**
 * 个人设置：账号信息 / 数据导出 / 注销账号。
 * 修改密码已独立为顶部用户菜单的弹窗入口，本页不再重复。
 */
export default function Settings({
  me,
  onLogout,
  onRefreshMe,
}: {
  me: Me;
  onLogout: () => void;
  onRefreshMe?: () => Promise<void> | void;
}) {
  const { message: msgApi } = App.useApp();
  const navigate = useNavigate();

  /* ---- 我的头像 ---- */
  const [draftAvatar, setDraftAvatar] = useState(me.avatar || '');
  const [avatarSaving, setAvatarSaving] = useState(false);

  /* ---- 上传自定义头像（待审核） ---- */
  const [uploading, setUploading] = useState(false);

  const handleUploadAvatar = async (file: File) => {
    setUploading(true);
    try {
      const blob = await compressImageToAvatar(file);
      await uploadAvatar(blob);
      await onRefreshMe?.();
      msgApi.success('已上传，等待管理员审核；审核通过前继续展示当前头像');
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '上传失败');
    } finally {
      setUploading(false);
    }
  };

  const handleSaveAvatar = async () => {
    setAvatarSaving(true);
    try {
      await changeAvatar(draftAvatar);
      await onRefreshMe?.();
      msgApi.success('头像已保存');
    } catch (e: unknown) {
      msgApi.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setAvatarSaving(false);
    }
  };

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
    <div className="dm-page" style={{ padding: '24px 32px', maxWidth: 760, margin: '0 auto' }}>
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

      {/* ---- 字号大小 ---- */}
      <Card>
        <Space align="center" style={{ marginBottom: 12 }}>
          <Title level={5} style={{ margin: 0 }}>字号大小</Title>
        </Space>
        <Segmented
          value={localStorage.getItem('dm_fontscale') || 'md'}
          onChange={(v) => {
            const val = String(v);
            localStorage.setItem('dm_fontscale', val);
            document.documentElement.dataset.fontscale = val;
            msgApi.success('字号已调整，全站即时生效');
          }}
          options={[
            { value: 'sm', label: '小' },
            { value: 'md', label: '标准' },
            { value: 'lg', label: '大' },
          ]}
        />
        <div style={{ marginTop: 8, color: '#8a94a6', fontSize: 12 }}>
          移动端基线已比 PC 略大；此设置在当前设备保存，即时生效。
        </div>
      </Card>

      <Divider />

      {/* ---- 我的头像 ---- */}
      <Card>
        <Space align="center" style={{ marginBottom: 16 }}>
          <UserAvatar avatar={draftAvatar} name={me.user} size={20} />
          <Title level={5} style={{ margin: 0 }}>我的头像</Title>
        </Space>
        <AvatarPicker
          value={draftAvatar}
          onChange={setDraftAvatar}
          username={me.user}
        />
        <Button
          type="primary"
          style={{ marginTop: 16 }}
          loading={avatarSaving}
          onClick={handleSaveAvatar}
        >
          保存头像
        </Button>
        <Divider style={{ margin: '16px 0' }} />
        <Space wrap>
          <Upload
            accept="image/png,image/jpeg,image/webp"
            showUploadList={false}
            beforeUpload={(f) => {
              handleUploadAvatar(f);
              return false;
            }}
          >
            <Button icon={<UploadOutlined />} loading={uploading}>
              上传自定义头像
            </Button>
          </Upload>
          {me.pending_avatar ? (
            <Tag color="orange">新头像审核中，当前展示旧头像</Tag>
          ) : (
            <Tag>自定义头像需管理员审核后生效</Tag>
          )}
        </Space>
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
