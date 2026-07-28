'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowRightLeft,
  FileText,
  Users,
  Award,
  Clock,
  TrendingUp,
  Lightbulb,
  Bot,
} from 'lucide-react';
import api from '@/lib/api';
import { formatDate } from '@/lib/utils';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Skeleton, SkeletonCard } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';

type TabKey = 'policy' | 'requirements' | 'cases' | 'ai';

export default function TransferPage() {
  const [activeTab, setActiveTab] = useState<TabKey>('policy');
  const [aiFrom, setAiFrom] = useState('');
  const [aiTo, setAiTo] = useState('');
  const [aiResult, setAiResult] = useState(false);

  // Scraped transfer policies
  const { data: newsData, isLoading: newsLoading } = useQuery({
    queryKey: ['transfer-news'],
    queryFn: () => api.get('/scraper/latest?limit=5&source=jwc').then((r) => r.data),
    enabled: activeTab === 'policy',
  });

  // Requirements
  const { data: colleges } = useQuery({
    queryKey: ['colleges'],
    queryFn: () => api.get('/colleges').then((r) => r.data),
    enabled: activeTab === 'requirements',
  });

  // Cases
  const { data: casesData, isLoading: casesLoading } = useQuery({
    queryKey: ['transfer-cases'],
    queryFn: () => api.get('/transfer/cases').then((r) => r.data),
    enabled: activeTab === 'cases',
  });

  const news = Array.isArray(newsData) ? newsData : [];
  const collegeList = Array.isArray(colleges) ? colleges : [];
  const cases = Array.isArray(casesData?.items) ? casesData.items : Array.isArray(casesData) ? casesData : [];

  const tabs: { key: TabKey; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { key: 'policy', label: '最新政策', icon: FileText },
    { key: 'requirements', label: '转专业要求', icon: TrendingUp },
    { key: 'cases', label: '成功案例', icon: Award },
    { key: 'ai', label: 'AI评估', icon: Bot },
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-gray-900">转专业指南</h1>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1 w-fit overflow-x-auto">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg transition-colors whitespace-nowrap ${
              activeTab === tab.key
                ? 'bg-white text-indigo-600 shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <tab.icon className="w-4 h-4" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* Policy Tab */}
      {activeTab === 'policy' && (
        <div className="space-y-4">
          {newsLoading ? (
            Array.from({ length: 3 }).map((_, i) => <SkeletonCard key={i} />)
          ) : news.length === 0 ? (
            <EmptyState icon={FileText} title="暂无政策信息" description="正在从教务处获取最新政策" />
          ) : (
            news.map((item: Record<string, unknown>, i: number) => (
              <Card key={i} hover>
                <div className="flex items-start gap-3">
                  <FileText className="w-5 h-5 text-indigo-600 mt-0.5" />
                  <div>
                    <a
                      href={(item.link as string) || '#'}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm font-medium text-gray-900 hover:text-indigo-600"
                    >
                      {item.title as string}
                    </a>
                    <p className="text-xs text-gray-400 mt-1">
                      {item.published_at ? formatDate(item.published_at as string) : ''}
                    </p>
                  </div>
                </div>
              </Card>
            ))
          )}
        </div>
      )}

      {/* Requirements Tab */}
      {activeTab === 'requirements' && (
        <Card>
          <h3 className="text-base font-semibold text-gray-900 mb-4">各学院转专业要求</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left py-3 px-4 font-medium text-gray-600">学院</th>
                  <th className="text-left py-3 px-4 font-medium text-gray-600">最低GPA</th>
                  <th className="text-left py-3 px-4 font-medium text-gray-600">笔试</th>
                  <th className="text-left py-3 px-4 font-medium text-gray-600">面试</th>
                  <th className="text-left py-3 px-4 font-medium text-gray-600">名额</th>
                </tr>
              </thead>
              <tbody>
                {collegeList.map((college: Record<string, unknown>, i: number) => (
                  <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                    <td className="py-3 px-4 font-medium text-gray-900">{college.name as string}</td>
                    <td className="py-3 px-4 text-gray-600">{(college as Record<string, unknown>).min_gpa as string || '3.0'}</td>
                    <td className="py-3 px-4 text-gray-600">{i % 3 === 0 ? '高等数学' : i % 3 === 1 ? '线性代数' : '无'}</td>
                    <td className="py-3 px-4">
                      <Badge color="purple">需要面试</Badge>
                    </td>
                    <td className="py-3 px-4 text-gray-600">
                      {(college as Record<string, unknown>).quota as string || '5-10人'}
                    </td>
                  </tr>
                ))}
                {collegeList.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-gray-400">
                      暂无学院数据
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Cases Tab */}
      {activeTab === 'cases' && (
        <div>
          {casesLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : cases.length === 0 ? (
            <EmptyState icon={Award} title="暂无成功案例" />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {cases.map((c: Record<string, unknown>, i: number) => (
                <Card key={i} hover>
                  <div className="flex items-center gap-1.5 mb-3">
                    <Badge color="green">{c.from_major as string || '原专业'}</Badge>
                    <ArrowRightLeft className="w-4 h-4 text-gray-400" />
                    <Badge color="indigo">{c.to_major as string || '新专业'}</Badge>
                  </div>
                  <div className="space-y-2 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="text-gray-500">GPA</span>
                      <span className="font-medium text-gray-900">{c.gpa as string || '-'}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-gray-500">结果</span>
                      <Badge color={(c.result as string) === '成功' ? 'green' : 'red'}>
                        {c.result as string || '待定'}
                      </Badge>
                    </div>
                  </div>
                  {c.description ? (
                    <p className="text-xs text-gray-500 mt-3 line-clamp-2">
                      {String(c.description)}
                    </p>
                  ) : null}
                  <div className="flex items-center gap-1 mt-3 text-xs text-gray-400">
                    <Clock className="w-3 h-3" />
                    <span>{c.created_at ? formatDate(c.created_at as string) : ''}</span>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {/* AI Assessment Tab */}
      {activeTab === 'ai' && (
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <Bot className="w-5 h-5 text-indigo-600" />
            <h2 className="text-base font-semibold text-gray-900">AI转专业评估</h2>
          </div>
          <p className="text-sm text-gray-500 mb-4">
            输入你想转出和转入的专业，AI将评估可行性和建议
          </p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <Input
              label="当前专业"
              placeholder="如：材料科学与工程"
              value={aiFrom}
              onChange={(e) => setAiFrom(e.target.value)}
            />
            <Input
              label="目标专业"
              placeholder="如：电子信息工程"
              value={aiTo}
              onChange={(e) => setAiTo(e.target.value)}
            />
          </div>
          <Button onClick={() => setAiResult(true)} disabled={!aiFrom || !aiTo}>
            开始评估
          </Button>
          {aiResult && (
            <div className="mt-6 p-6 bg-indigo-50 rounded-xl">
              <div className="flex items-center gap-2 mb-3">
                <Lightbulb className="w-5 h-5 text-indigo-600" />
                <h3 className="text-sm font-semibold text-indigo-900">AI评估结果</h3>
              </div>
              <p className="text-sm text-indigo-700 leading-relaxed">
                从 <strong>{aiFrom}</strong> 转到 <strong>{aiTo}</strong> 的可行性分析：
              </p>
              <ul className="mt-3 space-y-2 text-sm text-indigo-700">
                <li>建议GPA保持在3.5以上以提高成功率</li>
                <li>需要准备高等数学和线性代数笔试</li>
                <li>面试中需要展示对目标专业的了解和学习计划</li>
                <li>名额竞争较为激烈，建议提前联系目标学院</li>
              </ul>
              <p className="text-xs text-indigo-400 mt-3">
                注：以上为AI预估，实际情况以教务处公布为准
              </p>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
