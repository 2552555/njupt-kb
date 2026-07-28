'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  GraduationCap,
  BookOpen,
  Bot,
  School,
  Star,
  MapPin,
  Calendar,
  Award,
} from 'lucide-react';
import api from '@/lib/api';
import { formatDate } from '@/lib/utils';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Skeleton, SkeletonCard } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';
import { StarRating } from '@/components/ui/StarRating';

type TabKey = 'policies' | 'experiences' | 'ai-plan';

export default function BaoyanPage() {
  const [activeTab, setActiveTab] = useState<TabKey>('policies');
  const [selectedCollegeId, setSelectedCollegeId] = useState<number | null>(null);

  const { data: colleges } = useQuery({
    queryKey: ['colleges'],
    queryFn: () => api.get('/colleges').then((r) => r.data),
  });

  const { data: policiesData, isLoading: policiesLoading } = useQuery({
    queryKey: ['baoyan-policies', selectedCollegeId],
    queryFn: () => {
      const params = new URLSearchParams();
      params.set('year', '2026');
      if (selectedCollegeId) params.set('college_id', String(selectedCollegeId));
      return api.get(`/baoyan/policies?${params.toString()}`).then((r) => r.data);
    },
    enabled: activeTab === 'policies',
  });

  const { data: experiencesData, isLoading: expLoading } = useQuery({
    queryKey: ['baoyan-experiences'],
    queryFn: () => api.get('/baoyan/experiences').then((r) => r.data),
    enabled: activeTab === 'experiences',
  });

  const collegeList = Array.isArray(colleges) ? colleges : [];
  const policies = Array.isArray(policiesData?.items) ? policiesData.items : Array.isArray(policiesData) ? policiesData : [];
  const experiences = Array.isArray(experiencesData?.items) ? experiencesData.items : Array.isArray(experiencesData) ? experiencesData : [];

  const tabs: { key: TabKey; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { key: 'policies', label: '政策查询', icon: BookOpen },
    { key: 'experiences', label: '经验库', icon: Star },
    { key: 'ai-plan', label: 'AI路线规划', icon: Bot },
  ];

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-gray-900">保研中心</h1>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1 w-fit">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
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
      {activeTab === 'policies' && (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          {/* College selector */}
          <div className="lg:col-span-1">
            <Card padding="sm">
              <h3 className="text-sm font-semibold text-gray-900 mb-3">选择学院</h3>
              <div className="space-y-0.5 max-h-96 overflow-y-auto">
                <button
                  onClick={() => setSelectedCollegeId(null)}
                  className={`w-full text-left px-2 py-1.5 text-sm rounded-md transition-colors ${
                    !selectedCollegeId ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  全部学院
                </button>
                {collegeList.map((c: Record<string, unknown>) => (
                  <button
                    key={c.id as number}
                    onClick={() => setSelectedCollegeId(c.id as number)}
                    className={`w-full text-left px-2 py-1.5 text-sm rounded-md transition-colors ${
                      selectedCollegeId === c.id ? 'bg-indigo-50 text-indigo-700 font-medium' : 'text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {c.name as string}
                  </button>
                ))}
              </div>
            </Card>
          </div>

          {/* Policy cards */}
          <div className="lg:col-span-3 space-y-4">
            {policiesLoading ? (
              <SkeletonList count={3} />
            ) : policies.length === 0 ? (
              <EmptyState icon={BookOpen} title="暂无政策信息" description="未找到相关学院的保研政策" />
            ) : (
              policies.map((p: Record<string, unknown>, i: number) => (
                <Card key={i}>
                  <div className="flex items-start justify-between">
                    <div>
                      <h3 className="text-lg font-semibold text-gray-900">
                        {p.college_name as string}
                      </h3>
                      <p className="text-sm text-gray-500">
                        {p.year as string} 年保研政策
                      </p>
                    </div>
                    {p.quota ? <Badge color="purple">名额: {String(p.quota)}</Badge> : null}
                  </div>
                  {p.requirements ? (
                    <div className="mt-4 p-4 bg-gray-50 rounded-xl">
                      <h4 className="text-sm font-semibold text-gray-700 mb-2">推免条件</h4>
                      <p className="text-sm text-gray-600 leading-relaxed">{String(p.requirements)}</p>
                    </div>
                  ) : null}
                  {p.timeline ? (
                    <div className="mt-3 flex items-center gap-2 text-sm text-gray-500">
                      <Calendar className="w-4 h-4" />
                      <span>{String(p.timeline)}</span>
                    </div>
                  ) : null}
                </Card>
              ))
            )}
          </div>
        </div>
      )}

      {/* Experiences Tab */}
      {activeTab === 'experiences' && (
        <div>
          {expLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
            </div>
          ) : experiences.length === 0 ? (
            <EmptyState icon={Star} title="暂无经验分享" description="还没有学长学姐分享保研经验" />
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {experiences.map((exp: Record<string, unknown>, i: number) => (
                <Card key={i} hover>
                  <div className="flex items-center gap-2 mb-3">
                    <GraduationCap className="w-5 h-5 text-purple-600" />
                    <h3 className="text-sm font-semibold text-gray-900 line-clamp-1">
                      {exp.target_university as string}
                    </h3>
                  </div>
                  <div className="space-y-2 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="text-gray-500">GPA</span>
                      <span className="font-medium text-gray-900">{exp.gpa as string}</span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-gray-500">结果</span>
                      <Badge color={(exp.offer_result as string) === '录取' ? 'green' : 'red'}>
                        {exp.offer_result as string}
                      </Badge>
                    </div>
                  </div>
                  {exp.tags ? (
                    <div className="flex flex-wrap gap-1 mt-3">
                      {String(exp.tags).split(',').map((tag: string, j: number) => (
                        <Badge key={j} color="gray">{tag.trim()}</Badge>
                      ))}
                    </div>
                  ) : null}
                  <div className="flex items-center gap-1 mt-3 text-xs text-gray-400">
                    <MapPin className="w-3 h-3" />
                    <span>{exp.target_university as string}</span>
                    <span className="mx-1">|</span>
                    <span>{formatDate(exp.created_at as string)}</span>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}

      {/* AI Plan Tab */}
      {activeTab === 'ai-plan' && (
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <Bot className="w-5 h-5 text-indigo-600" />
            <h2 className="text-base font-semibold text-gray-900">AI路线规划</h2>
          </div>
          <p className="text-sm text-gray-500 mb-4">
            输入你的基本信息，AI将为你规划个性化保研路线
          </p>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <Input label="年级" placeholder="如：大一、大二、大三" />
            <Input label="专业" placeholder="如：电子信息工程" />
            <Input label="目标院校" placeholder="如：清华大学" />
          </div>
          <Button variant="primary">生成规划</Button>
          <div className="mt-6 p-6 bg-purple-50 rounded-xl text-center">
            <Bot className="w-10 h-10 text-purple-400 mx-auto mb-3" />
            <p className="text-sm text-purple-700 font-medium">AI规划结果将在这里显示</p>
            <p className="text-xs text-purple-500 mt-1">点击"生成规划"获取个性化建议</p>
          </div>
        </Card>
      )}
    </div>
  );
}

function SkeletonList({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-4">
      {Array.from({ length: count }).map((_, i) => <SkeletonCard key={i} />)}
    </div>
  );
}
