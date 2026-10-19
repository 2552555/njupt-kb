'use client';

import type { ReactNode } from 'react';

/**
 * 内联 SVG 趋势图（beta16 §43）：不引入图表库，`package.json` 保持不变。
 *
 * - 只画坐标基线、网格与数据点；数值通过 `<title>` 在悬停时显示。
 * - 空数据（全 0 或无日期）渲染空态，不计算比例，避免出现 NaN。
 * - 入场动效写在 globals.css，仅在 `prefers-reduced-motion: no-preference` 时启用。
 */
export interface TrendSeries {
  label: string;
  /** CSS 颜色值（如 `var(--dn-emerald)`）：只用于图形，不用于正文。 */
  color: string;
  values: number[];
}

export interface TrendChartProps {
  title: string;
  description?: string;
  /** UTC 自然日，YYYY-MM-DD。 */
  dates: string[];
  series: TrendSeries[];
  kind?: 'bar' | 'line';
  unit?: string;
  footer?: ReactNode;
}

const WIDTH = 640;
const HEIGHT = 240;
const PAD = { top: 16, right: 16, bottom: 32, left: 48 };
const PLOT_W = WIDTH - PAD.left - PAD.right;
const PLOT_H = HEIGHT - PAD.top - PAD.bottom;

function safeValue(value: number): number {
  return Number.isFinite(value) && value > 0 ? value : 0;
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + safeValue(value), 0);
}

function formatTick(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function xLabelIndices(count: number): number[] {
  if (count <= 1) return count === 1 ? [0] : [];
  const step = Math.max(1, Math.ceil(count / 4));
  const indices: number[] = [];
  for (let index = 0; index < count; index += step) indices.push(index);
  const last = count - 1;
  if (indices[indices.length - 1] !== last) indices.push(last);
  return indices;
}

export function TrendChart({
  title,
  description,
  dates,
  series,
  kind = 'line',
  unit = '',
  footer,
}: TrendChartProps) {
  const count = dates.length;
  const values = series.flatMap((item) => item.values.map(safeValue));
  const max = Math.max(0, ...values);
  const empty = count === 0 || max <= 0;

  const totalText = series
    .map((item) => `${item.label}合计 ${sum(item.values)}${unit}`)
    .join('，');
  const summary =
    count === 0
      ? `${title}：窗口内没有数据`
      : `${title}：${dates[0]} 至 ${dates[count - 1]}，${totalText}`;

  return (
    <figure className="border border-divider bg-surface p-4">
      <figcaption>
        <h3 className="dn-display text-lg text-text-primary">{title}</h3>
        {description && <p className="mt-1 text-xs text-text-secondary">{description}</p>}
        {!empty && (
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
            {series.map((item) => (
              <li key={item.label} className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="inline-block h-2.5 w-2.5"
                  style={{ backgroundColor: item.color }}
                />
                {item.label} · 合计 {sum(item.values)}
                {unit}
              </li>
            ))}
          </ul>
        )}
      </figcaption>

      {empty ? (
        <div className="mt-3 flex h-40 items-center justify-center border border-divider bg-canvas px-4 text-center text-sm text-text-secondary">
          窗口内暂无数据。切换更长的窗口或等待新的记录后再看。
        </div>
      ) : (
        <div className="mt-3">
          <svg
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            className="h-auto w-full"
            role="img"
            aria-label={summary}
          >
            <title>{summary}</title>

            {/* 坐标基线：0 值、半程与峰值；文字用次要语义色。 */}
            {[0, 0.5, 1].map((ratio) => {
              const y = PAD.top + PLOT_H * (1 - ratio);
              return (
                <g key={ratio}>
                  <line
                    x1={PAD.left}
                    x2={WIDTH - PAD.right}
                    y1={y}
                    y2={y}
                    stroke="var(--dn-divider)"
                    strokeWidth={ratio === 0 ? 1.5 : 1}
                  />
                  <text
                    x={PAD.left - 8}
                    y={y + 4}
                    textAnchor="end"
                    fontSize="11"
                    fill="var(--dn-text-secondary)"
                  >
                    {formatTick(max * ratio)}
                  </text>
                </g>
              );
            })}

            {/* 横轴日期：只标注首尾与间隔点，避免 30 天窗口挤成一团。 */}
            {xLabelIndices(count).map((index) => {
              const x =
                kind === 'bar'
                  ? PAD.left + (PLOT_W / count) * (index + 0.5)
                  : count === 1
                    ? PAD.left + PLOT_W / 2
                    : PAD.left + (PLOT_W / (count - 1)) * index;
              return (
                <text
                  key={dates[index]}
                  x={x}
                  y={HEIGHT - 10}
                  textAnchor="middle"
                  fontSize="11"
                  fill="var(--dn-text-secondary)"
                >
                  {dates[index].slice(5)}
                </text>
              );
            })}

            {kind === 'bar' ? (
              <Bars dates={dates} series={series} max={max} />
            ) : (
              <Lines dates={dates} series={series} max={max} unit={unit} />
            )}
          </svg>
        </div>
      )}

      {footer && <div className="mt-3 text-xs text-text-secondary">{footer}</div>}
    </figure>
  );
}

function Lines({
  dates,
  series,
  max,
  unit,
}: {
  dates: string[];
  series: TrendSeries[];
  max: number;
  unit: string;
}) {
  const count = dates.length;
  const xAt = (index: number) =>
    count === 1 ? PAD.left + PLOT_W / 2 : PAD.left + (PLOT_W / (count - 1)) * index;
  const yAt = (value: number) => PAD.top + PLOT_H * (1 - safeValue(value) / max);

  return (
    <g>
      {series.map((item) => {
        const path = item.values
          .map((value, index) => `${index === 0 ? 'M' : 'L'}${xAt(index)},${yAt(value)}`)
          .join(' ');
        return (
          <g key={item.label}>
            <path
              className="dn-chart-line"
              d={path}
              fill="none"
              stroke={item.color}
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
              pathLength={1}
            />
            {item.values.map((value, index) => (
              <g key={`${item.label}-${dates[index]}`}>
                <circle cx={xAt(index)} cy={yAt(value)} r="10" fill="transparent">
                  <title>{`${dates[index]} · ${item.label}：${safeValue(value)}${unit}`}</title>
                </circle>
                <circle
                  cx={xAt(index)}
                  cy={yAt(value)}
                  r="3"
                  fill={item.color}
                  pointerEvents="none"
                />
              </g>
            ))}
          </g>
        );
      })}
    </g>
  );
}

function Bars({
  dates,
  series,
  max,
}: {
  dates: string[];
  series: TrendSeries[];
  max: number;
}) {
  const count = dates.length;
  const groupWidth = PLOT_W / count;
  const barWidth = Math.max(2, Math.min(18, (groupWidth - 6) / Math.max(1, series.length)));

  return (
    <g>
      {series.map((item, seriesIndex) => (
        <g key={item.label}>
          {item.values.map((value, index) => {
            const safe = safeValue(value);
            const height = (safe / max) * PLOT_H;
            const groupStart = PAD.left + groupWidth * index;
            const startX =
              groupStart + (groupWidth - barWidth * series.length - 2 * (series.length - 1)) / 2;
            const x = startX + seriesIndex * (barWidth + 2);
            return (
              <rect
                key={`${item.label}-${dates[index]}`}
                className="dn-chart-bar"
                x={x}
                y={PAD.top + PLOT_H - height}
                width={barWidth}
                height={height}
                fill={item.color}
                style={{ animationDelay: `${index * 18}ms` }}
              >
                <title>{`${dates[index]} · ${item.label}：${safe}`}</title>
              </rect>
            );
          })}
        </g>
      ))}
    </g>
  );
}
