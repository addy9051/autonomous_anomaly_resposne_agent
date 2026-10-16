import React from "react";

type IconProps = { size?: number; className?: string };

export function PrometheusIcon({ size = 40, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" className={className} aria-label="Prometheus">
      <circle cx="24" cy="24" r="22" fill="#E6522C" />
      <path d="M24 9c3 4 1 6 3 9 1.500 2.200 4 4 4 8a7 7 0 0 1-14 0c0-2 .800-3.500 2-5 .200 1.800 1 2.500 2 2.500-1-4 0-6 1.500-8C23.500 13 24 11 24 9z" fill="#fff" />
      <rect x="15" y="35" width="18" height="2.500" rx="1.250" fill="#fff" />
      <rect x="17" y="39" width="14" height="2.500" rx="1.250" fill="#fff" />
    </svg>
  );
}

export function DatadogIcon({ size = 40, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" className={className} aria-label="Datadog">
      <rect x="2" y="2" width="44" height="44" rx="12" fill="#632CA6" />
      <path d="M14 30c2 3 6 5 10 5s8-2 10-5l-3-9-4 2-3-6-4 5-4-2z" fill="#fff" />
      <circle cx="20.500" cy="25" r="1.800" fill="#632CA6" />
      <circle cx="27.500" cy="25" r="1.800" fill="#632CA6" />
    </svg>
  );
}

export function SplunkIcon({ size = 40, className }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" className={className} aria-label="Splunk">
      <rect x="2" y="2" width="44" height="44" rx="10" fill="#0E1116" stroke="#65A637" strokeWidth="2" />
      <path d="M15 14l16 10-16 10v-5l8-5-8-5z" fill="#65A637" />
      <path d="M26 14l8 5-8 5z" fill="#65A637" opacity="0.6" />
    </svg>
  );
}

export function ConnectorIcon({ id, size }: { id: string; size?: number }) {
  switch (id) {
    case "prometheus":
      return <PrometheusIcon size={size} />;
    case "datadog":
      return <DatadogIcon size={size} />;
    case "splunk":
      return <SplunkIcon size={size} />;
    default:
      return <div style={{ width: size ?? 40, height: size ?? 40 }} className="rounded-xl bg-white/10" />;
  }
}
