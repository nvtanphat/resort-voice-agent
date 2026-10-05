import React from 'react';
import type { ServiceItem } from '../types';
import type { LanguageCode, RequestKind } from '../api';
import {t} from '../i18n';

const safeIcon=(value:string)=>/^[a-z][a-z0-9_]{0,47}$/.test(value)?value:'info';

interface SidebarNavProps {
  items: ServiceItem[];
  language:LanguageCode;
  activeId: string;
  onSelectItem: (id: string) => void;
}

export const SidebarNav: React.FC<SidebarNavProps> = ({
  items,
  language,
  activeId,
  onSelectItem,
}) => {
  return (
    <aside
      className="lg:col-span-3 flex flex-col justify-between h-full overflow-hidden pb-1"
      data-purpose="navigation-sidebar"
    >
      <div className="space-y-1.5 overflow-y-auto custom-scrollbar flex-1 pr-1">
        {items.map((item) => {
          const isActive = item.id === activeId;
          return (
            <button
              type="button"
              key={item.id}
              onClick={() => onSelectItem(item.id)}
              className={`group w-full min-h-11 text-left flex items-center justify-between p-2.5 rounded-xl cursor-pointer transition ${
                isActive
                  ? 'bg-[#11243A] text-white shadow border border-white/10 hover:border-white/30'
                  : 'bg-[#FCFAF6] hover:bg-white text-brand-textDark border border-brand-borderLight shadow-sm hover:border-[#D9CEBC]'
              }`}
            >
              <div className="flex items-center space-x-3.5">
                <div
                  className={`w-11 h-11 rounded-lg flex-shrink-0 shadow-sm border flex items-center justify-center ${
                    isActive ? 'border-white/20 bg-white/10' : 'border-stone-200 bg-white'
                  }`}
                  aria-hidden="true"
                >
                  <span className={`material-symbols-outlined text-[22px] ${isActive ? 'text-amber-300' : 'text-[#7A5B35]'}`}>
                    {safeIcon(item.iconCategory)}
                  </span>
                </div>
                <div className="leading-snug">
                  <div className="flex items-center space-x-2">
                    <span
                      className={`font-serif font-semibold text-[15px] ${
                        isActive ? 'tracking-wide text-white' : 'text-[#142742]'
                      }`}
                    >
                      {item.title}
                    </span>
                  </div>
                  <p
                    className={`text-xs mt-0.5 leading-snug line-clamp-2 ${
                      isActive ? 'text-slate-300' : 'text-gray-500'
                    }`}
                  >
                    {item.description}
                  </p>
                </div>
              </div>
              <span
                className={`material-symbols-outlined group-hover:translate-x-0.5 transition text-[18px] mr-1 ${
                  isActive ? 'text-white/80' : 'text-slate-400'
                }`}
              >
                chevron_right
              </span>
            </button>
          );
        })}
      </div>

      {/* Decorative navigation footer; no property claims */}
      <div className="py-2 text-center text-[10px] tracking-[0.35em] text-[#8A8275] uppercase font-sans font-medium border-t border-brand-borderLight/60">
        {t(language,'navigationFooter')}
      </div>
    </aside>
  );
};
