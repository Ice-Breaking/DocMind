import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  ApiOutlined,
  CommentOutlined,
  DatabaseOutlined,
  MessageOutlined,
  PlusOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  Button,
  Card,
  Col,
  Empty,
  List,
  Result,
  Row,
  Spin,
  Statistic,
  Steps,
  Typography,
} from 'antd';
import { fetchDashboard, type DashboardStats, type Me, type Session } from '../api';
import { titleFromContent } from './chat/utils';

/** 秒级时间戳 → 本地可读时间 */
function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

export default function Dashboard({ me }: { me: Me }) {
  const navigate = useNavigate();

  // 工作台统计数据：TanStack Query 托管加载/错误态与请求去重
  // （StrictMode 双挂载不再双请求；行为对齐旧手写 effect：进入即拉取、失败即错误态）
  const { data: stats, isPending, error } = useQuery<DashboardStats, Error>({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
  });

  /* ---- loading / error ---- */
  if (isPending) {
    return <Spin style={{ display: 'block', margin: '120px auto' }} size="large" />;
  }
  if (error || !stats) {
    return (
      <Result
        status="warning"
        title="仪表盘加载失败"
        subTitle={error?.message || '未知错误'}
        extra={<Button type="primary" onClick={() => window.location.reload()}>重试</Button>}
      />
    );
  }

  const recentSessions: Session[] = stats.recent_sessions || [];
  const showOnboarding = recentSessions.length === 0;

  return (
    <div className="dm-page" style={{ padding: '24px 32px', maxWidth: 1200, margin: '0 auto' }}>
      {/* ---- 页头 ---- */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 24,
        }}
      >
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>工作台</h1>
          <Typography.Text type="secondary">
            你好，{me.user}，欢迎使用 DocMind
          </Typography.Text>
        </div>
        <Button
          type="primary"
          size="large"
          icon={<PlusOutlined />}
          onClick={() => navigate('/chat')}
        >
          快速提问
        </Button>
      </div>

      {/* ---- 首次使用引导 ---- */}
      {showOnboarding && (
        <Card style={{ marginBottom: 16 }}>
          <Steps
            size="small"
            current={0}
            items={[
              {
                title: '建知识库',
                description: '上传文档，构建专属知识库',
                icon: <DatabaseOutlined />,
              },
              {
                title: '建助手',
                description: '基于知识库创建智能助手',
                icon: <RobotOutlined />,
              },
              {
                title: '开始对话',
                description: '向助手提问，获取有据回答',
                icon: <CommentOutlined />,
              },
            ]}
          />
        </Card>
      )}

      {/* ---- 统计卡片 ---- */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="总问答数" value={stats.total_messages} prefix={<MessageOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic title="今日调用" value={stats.today_calls} prefix={<ApiOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="缓存命中率"
              value={(stats.cache_hit_rate * 100).toFixed(1)}
              suffix="%"
              prefix={<ThunderboltOutlined />}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="待处理 Badcase"
              value={stats.badcase_pending}
              valueStyle={stats.badcase_pending > 0 ? { color: '#cf1322' } : undefined}
              prefix={<WarningOutlined />}
            />
          </Card>
        </Col>
      </Row>

      {/* ---- 最近会话 ---- */}
      <Card
        title="最近会话"
        style={{ marginTop: 16 }}
        extra={
          <Button size="small" onClick={() => navigate('/sessions')}>
            查看全部
          </Button>
        }
      >
        {recentSessions.length === 0 ? (
          <Empty
            description="暂无会话，点击右上角「快速提问」开始"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          <List
            dataSource={recentSessions}
            rowKey="id"
            renderItem={(s: Session) => (
              <List.Item
                style={{ cursor: 'pointer' }}
                onClick={() => navigate('/sessions')}
                actions={[<span key="count">{s.msg_count} 条消息</span>]}
              >
                <List.Item.Meta
                  title={titleFromContent(s.title) || '（未命名会话）'}
                  description={`更新于 ${formatTime(s.updated_at)}`}
                />
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  );
}
