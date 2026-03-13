/** Nodeglow design system color palette */
export const colors = {
  bg: { DEFAULT: '#0B0E14', surface: '#111621', elevated: '#1A1F2E' },
  border: { DEFAULT: '#1E2433', hover: '#2A3144' },
  primary: { DEFAULT: '#38BDF8', light: '#7DD3FC', dim: '#0C4A6E' },
  accent: { DEFAULT: '#A78BFA', light: '#C4B5FD', dim: '#4C1D95' },
  success: { DEFAULT: '#34D399', light: '#6EE7B7', dim: '#064E3B' },
  warning: { DEFAULT: '#FBBF24', light: '#FDE68A', dim: '#78350F' },
  critical: { DEFAULT: '#F87171', light: '#FCA5A5', dim: '#7F1D1D' },
  info: { DEFAULT: '#60A5FA', light: '#93C5FD', dim: '#1E3A5F' },
  text: {
    primary: '#F1F5F9',
    secondary: '#94A3B8',
    muted: '#64748B',
    disabled: '#475569',
  },
  integrations: {
    proxmox: '#E57000', unifi: '#0559C9', unas: '#06B6D4', pihole: '#DC2626',
    adguard: '#10B981', portainer: '#0DB7ED', truenas: '#475569', synology: '#2563EB',
    firewall: '#EA580C', hass: '#F59E0B', gitea: '#16A34A', phpipam: '#9333EA',
    speedtest: '#3B82F6', ups: '#EAB308', redfish: '#7C3AED',
  },
} as const;

export type IntegrationType = keyof typeof colors.integrations;
