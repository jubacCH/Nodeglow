'use client';

import { SegmentedControl } from '@/components/ui/Tabs';
import { useThemeStore } from '@/stores/theme';
import type { ColorMode } from '@/lib/theme';
import { SettingsSection } from './formKit';

type FontKey = 'sm' | 'base' | 'lg';
const FONT_PX: Record<FontKey, number> = { sm: 12, base: 14, lg: 16 };

/**
 * Personal display preferences. They live in this browser only and apply
 * immediately (the old tab mixed instant theme switching with a separate
 * "Save preferences" button for density and font size).
 */
export function AppearanceTab() {
  const colorMode = useThemeStore((s) => s.colorMode);
  const setColorMode = useThemeStore((s) => s.setColorMode);
  const density = useThemeStore((s) => s.density);
  const setDensity = useThemeStore((s) => s.setDensity);
  const fontSize = useThemeStore((s) => s.fontSize);
  const setFontSize = useThemeStore((s) => s.setFontSize);
  const fontKey: FontKey = fontSize <= 12 ? 'sm' : fontSize >= 16 ? 'lg' : 'base';

  return (
    <div className="space-y-4">
      <p className="text-ui text-fg-2">These preferences apply immediately and are stored in this browser only.</p>
      <SettingsSection
        id="theme"
        title="Theme"
        description="“System” follows your operating system. The accent colour is fixed (Glow Violet)."
      >
        <SegmentedControl<ColorMode>
          label="Theme"
          value={colorMode}
          onChange={setColorMode}
          options={[
            { value: 'dark', label: 'Dark' },
            { value: 'light', label: 'Light' },
            { value: 'system', label: 'System' },
          ]}
        />
      </SettingsSection>

      <SettingsSection id="density" title="Density" description="Compact reduces spacing in page content.">
        <SegmentedControl<'comfortable' | 'compact'>
          label="Density"
          value={density}
          onChange={setDensity}
          options={[
            { value: 'comfortable', label: 'Comfortable' },
            { value: 'compact', label: 'Compact' },
          ]}
        />
      </SettingsSection>

      <SettingsSection id="font-size" title="Font size" description="Scales all text in the interface.">
        <SegmentedControl<FontKey>
          label="Font size"
          value={fontKey}
          onChange={(k) => setFontSize(FONT_PX[k])}
          options={[
            { value: 'sm', label: 'Small' },
            { value: 'base', label: 'Default' },
            { value: 'lg', label: 'Large' },
          ]}
        />
      </SettingsSection>
    </div>
  );
}
