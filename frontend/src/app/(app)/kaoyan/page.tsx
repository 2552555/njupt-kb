'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BookOpen,
  School,
  TrendingUp,
  Users,
  UserCheck,
  BarChart3,
} from 'lucide-react';
import api from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Skeleton, SkeletonCard } from '@/components/ui/Skeleton';
import { EmptyState } from '@/components/ui/EmptyState';

export default function KaoyanPage() {
  const [selectedSchoolId, setSelectedSchoolId] = useState<number | null>(null);
  const [selectedMajorId, setSelectedMajorId] = useState<number | null>(null);

  const { data: schoolsData, isLoading: schoolsLoading } = useQuery({
    queryKey: ['kaoyan-schools'],
    queryFn: () => api.get('/kaoyan/schools').then((r) => r.data),
  });

  const { data: majorsData, isLoading: majorsLoading } = useQuery({
    queryKey: ['kaoyan-majors', selectedSchoolId],
    queryFn: () => api.get(`/kaoyan/schools/${selectedSchoolId}/majors`).then((r) => r.data),
    enabled: !!selectedSchoolId,
  });

  const { data: scoreLinesData, isLoading: scoresLoading } = useQuery({
    queryKey: ['kaoyan-scores', selectedSchoolId, selectedMajorId],
    queryFn: () =>
      api
        .get(`/kaoyan/score-lines?school_id=${selectedSchoolId}&major_id=${selectedMajorId}`)
        .then((r) => r.data),
    enabled: !!selectedSchoolId && !!selectedMajorId,
  });

  const schools = Array.isArray(schoolsData?.items) ? schoolsData.items : Array.isArray(schoolsData) ? schoolsData : [];
  const majors = Array.isArray(majorsData?.items) ? majorsData.items : Array.isArray(majorsData) ? majorsData : [];
  const scoreLines = Array.isArray(scoreLinesData?.items) ? scoreLinesData.items : Array.isArray(scoreLinesData) ? scoreLinesData : [];

  const selectedSchool = schools.find((s: Record<string, unknown>) => s.id === selectedSchoolId);
  const selectedMajor = majors.find((m: Record<string, unknown>) => m.id === selectedMajorId);

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-gray-900">考研中心</h1>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* School Selector */}
        <Card>
          <h3 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
            <School className="w-4 h-4 text-indigo-600" /> 选择院校
          </h3>
          {schoolsLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : (
            <div className="space-y-1 max-h-80 overflow-y-auto">
              {schools.map((s: Record<string, unknown>) => (
                <button
                  key={s.id as number}
                  onClick={() => {
                    setSelectedSchoolId(s.id as number);
                    setSelectedMajorId(null);
                  }}
                  className={`w-full text-left px-3 py-2 text-sm rounded-lg transition-colors ${
                    selectedSchoolId === s.id
                      ? 'bg-indigo-50 text-indigo-700 font-medium'
                      : 'text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  {s.name as string}
                </button>
              ))}
              {schools.length === 0 && (
                <p className="text-sm text-gray-400 text-center py-4">暂无院校数据</p>
              )}
            </div>
          )}
        </Card>

        {/* Major Selector */}
        <Card>
          <h3 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-indigo-600" /> 选择专业
          </h3>
          {!selectedSchoolId ? (
            <EmptyState icon={BookOpen} title="请先选择院校" description="在左侧选择院校后可查看专业列表" />
          ) : majorsLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : (
            <div className="space-y-1 max-h-80 overflow-y-auto">
              {majors.map((m: Record<string, unknown>) => (
                <button
                  key={m.id as number}
                  onClick={() => setSelectedMajorId(m.id as number)}
                  className={`w-full text-left px-3 py-2 text-sm rounded-lg transition-colors ${
                    selectedMajorId === m.id
                      ? 'bg-indigo-50 text-indigo-700 font-medium'
                      : 'text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  {m.name as string}
                </button>
              ))}
              {majors.length === 0 && (
                <p className="text-sm text-gray-400 text-center py-4">暂无专业数据</p>
              )}
            </div>
          )}
        </Card>
      </div>

      {/* Score Lines */}
      {selectedSchool && selectedMajor && (
        <Card>
          <h3 className="text-base font-semibold text-gray-900 mb-4">
            {selectedSchool.name as string} - {selectedMajor.name as string} 复试分数线
          </h3>
          {scoresLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          ) : scoreLines.length === 0 ? (
            <EmptyState icon={BarChart3} title="暂无分数线数据" />
          ) : (
            <>
              {/* Table */}
              <div className="overflow-x-auto mb-6">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-gray-200">
                      <th className="text-left py-3 px-4 font-medium text-gray-600">年份</th>
                      <th className="text-left py-3 px-4 font-medium text-gray-600">总分线</th>
                      <th className="text-left py-3 px-4 font-medium text-gray-600">政治</th>
                      <th className="text-left py-3 px-4 font-medium text-gray-600">英语</th>
                      <th className="text-left py-3 px-4 font-medium text-gray-600">数学</th>
                      <th className="text-left py-3 px-4 font-medium text-gray-600">专业课</th>
                      <th className="text-left py-3 px-4 font-medium text-gray-600">报考人数</th>
                      <th className="text-left py-3 px-4 font-medium text-gray-600">录取人数</th>
                      <th className="text-left py-3 px-4 font-medium text-gray-600">报录比</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scoreLines.map((sl: Record<string, unknown>, i: number) => (
                      <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                        <td className="py-3 px-4 font-medium text-gray-900">{sl.year as string || '-'}</td>
                        <td className="py-3 px-4">
                          <Badge color="indigo">{sl.total_score_line as string || '-'}</Badge>
                        </td>
                        <td className="py-3 px-4 text-gray-600">{sl.politics_line as string || '-'}</td>
                        <td className="py-3 px-4 text-gray-600">{sl.english_line as string || '-'}</td>
                        <td className="py-3 px-4 text-gray-600">{sl.math_line as string || '-'}</td>
                        <td className="py-3 px-4 text-gray-600">{sl.major_line as string || '-'}</td>
                        <td className="py-3 px-4 text-gray-600">{sl.applicants as string || '-'}</td>
                        <td className="py-3 px-4 text-gray-600">{sl.admitted as string || '-'}</td>
                        <td className="py-3 px-4">
                          {sl.applicants && sl.admitted ? (
                            <Badge
                              color={
                                (sl.admitted as number) / (sl.applicants as number) > 0.3
                                  ? 'green'
                                  : (sl.admitted as number) / (sl.applicants as number) > 0.1
                                  ? 'yellow'
                                  : 'red'
                              }
                            >
                              {((sl.admitted as number) / (sl.applicants as number) * 100).toFixed(1)}%
                            </Badge>
                          ) : (
                            '-'
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Simple bar chart visualization */}
              <div>
                <h4 className="text-sm font-semibold text-gray-900 mb-3 flex items-center gap-2">
                  <BarChart3 className="w-4 h-4 text-indigo-600" /> 分数线趋势
                </h4>
                <div className="flex items-end gap-4 h-40 px-4">
                  {scoreLines.map((sl: Record<string, unknown>, i: number) => {
                    const score = parseInt((sl.total_score_line as string) || '0', 10);
                    const maxScore = 500;
                    const height = Math.max(8, (score / maxScore) * 100);
                    return (
                      <div key={i} className="flex flex-col items-center gap-1 flex-1">
                        <span className="text-xs font-medium text-gray-700">{score}</span>
                        <div
                          className="w-full max-w-[60px] bg-indigo-500 rounded-t-lg transition-all"
                          style={{ height: `${height}%` }}
                        />
                        <span className="text-xs text-gray-400">{sl.year as string}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </>
          )}
        </Card>
      )}
    </div>
  );
}
