import { useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  App,
  Button,
  Card,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import { CloudUploadOutlined, ReloadOutlined, SafetyOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { createBackup, fetchBackups, type BackupItem } from '../api';

const { Text } = Typography;

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/**
 * 备份与恢复：一键备份（数据库热备 + 全部知识库文档），存 data/backups/。
 * 恢复为人工演练流程（停服 → 解压覆盖 → 重启），页面给出步骤说明。
 */
export default function Backups() {
  const { message: msgApi } = App.useApp();
  const queryClient = useQueryClient();

  // 备份列表：失败保留上次数据仅弹 toast（对齐旧 load() 语义）
  const {
    data: backups = [],
    isPending: loading,
    error,
    refetch,
  } = useQuery<BackupItem[], Error>({ queryKey: ['backups'], queryFn: fetchBackups });

  /* 加载失败提示：与旧实现一致走全局 message */
  useEffect(() => {
    if (error) msgApi.error(error.message || '加载失败');
  }, [error, msgApi]);

  // 立即备份：成功后失效列表自动重拉（等价旧「toast + await load()」）
  const createMut = useMutation({
    mutationFn: createBackup,
    onSuccess: (r) => {
      msgApi.success(`备份完成：${r.name}（${r.files} 个文件 / ${formatSize(r.size)}）`);
      queryClient.invalidateQueries({ queryKey: ['backups'] });
    },
    onError: (e: Error) => msgApi.error(e.message || '备份失败'),
  });

  const columns: ColumnsType<BackupItem> = [
    {
      title: '备份文件',
      dataIndex: 'name',
      key: 'name',
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
      width: 120,
      render: (v: number) => formatSize(v),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 190,
      render: (ts: number) => new Date(ts * 1000).toLocaleString(),
    },
    {
      title: '位置',
      key: 'path',
      width: 160,
      render: () => <Tag>data/backups/</Tag>,
    },
  ];

  return (
    <div className="dm-page" style={{ padding: '24px 32px', maxWidth: 1200, margin: '0 auto' }}>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 24,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>备份与恢复</h1>
          <Text type="secondary">数据库热备（VACUUM INTO）+ 知识库文档打包，建议每日或重大变更前执行</Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>刷新</Button>
          <Button
            type="primary"
            icon={<CloudUploadOutlined />}
            loading={createMut.isPending}
            onClick={() => createMut.mutate()}
          >
            立即备份
          </Button>
        </Space>
      </div>

      <Table scroll={{ x: "max-content" }}
        columns={columns}
        dataSource={backups}
        rowKey="name"
        loading={loading}
        pagination={{ pageSize: 10 }}
        size="middle"
        locale={{ emptyText: '暂无备份，点击右上角「立即备份」' }}
      />

      <Card title={<Space><SafetyOutlined />恢复演练步骤</Space>} size="small" style={{ marginTop: 16 }}>
        <ol style={{ margin: 0, paddingLeft: 20, lineHeight: 2, fontSize: 13 }}>
          <li>停止服务（<Text code>Ctrl+C</Text> 或 <Text code>docker compose down</Text>）</li>
          <li>解压目标备份 zip：<Text code>unzip backup_YYYYmmdd_HHMMSS.zip -d 恢复目录</Text></li>
          <li>用解压出的 <Text code>chat.db</Text> 覆盖 <Text code>data/chat.db</Text>（先移走旧文件）</li>
          <li>按 zip 内路径还原文档目录（<Text code>docs/knowledge/</Text>、<Text code>data/kb_docs/</Text>）</li>
          <li>重启服务，到「知识库」页重建索引并抽查问答验证</li>
        </ol>
      </Card>

      <Alert
        type="info"
        showIcon
        style={{ marginTop: 12 }}
        message="备份文件与审计日志、知识库文档一起构成企业合规的数据底座；生产环境建议将 data/backups/ 同步到异地存储（如对象存储 / 定时 scp）。"
      />
    </div>
  );
}
