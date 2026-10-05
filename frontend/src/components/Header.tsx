import React from 'react';
import type { LanguageCode, UiLanguage } from '../api';
import {t} from '../i18n';

interface HeaderProps {
  currentLanguage: LanguageCode;
  languages: UiLanguage[];
  propertyName: string;
  now: Date;
  onLanguageChange: (lang: LanguageCode) => void;
  onEndSession: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  currentLanguage,
  languages,
  propertyName,
  now,
  onLanguageChange,
  onEndSession,
}) => {
  return (
    <header
      className="relative w-full h-[90px] flex-shrink-0 bg-[#0E1B2B] text-white flex items-center justify-between pl-0 pr-4 sm:pr-8 lg:pr-12 shadow-md overflow-hidden z-20"
      data-purpose="top-navigation-bar"
    >
      {/* Background Panorama Image Overlay on Right - Local HD 1376x768 WebP */}
      <div className="absolute inset-0 z-0 pointer-events-none overflow-hidden">
        <picture>
          <source srcSet="/static/header-bg.webp" type="image/webp" />
          <img
            alt={t(currentLanguage,'imageAlt')}
            className="w-full h-full object-cover object-[center_35%]"
            src="/static/header-bg-original.jpg"
          />
        </picture>
        <div
          className="absolute inset-0"
          style={{
            background:
              'linear-gradient(to right, rgba(13, 26, 41, 0.88) 0%, rgba(14, 29, 46, 0.65) 30%, rgba(14, 29, 46, 0.1) 60%, rgba(0, 0, 0, 0) 100%)',
          }}
        />
      </div>

      {/* Left: property-neutral concierge branding */}
      <div className="relative z-10 flex items-center h-full space-x-7">
        {/* Unbranded concierge mark and configurable property name */}
        <div className="flex items-center h-full px-6 sm:px-8 bg-white shadow-md border-r border-brand-borderLight space-x-4 text-[#142742]">
          {/* Decorative jewel mark */}
          <svg aria-hidden="true" focusable="false" className="w-11 h-11 flex-shrink-0 drop-shadow-sm" viewBox="0 0 100 100" fill="none">
            <polygon points="50,6 78,22 50,50" fill="#626896" />
            <polygon points="50,6 22,22 50,50" fill="#7A81B0" />
            <polygon points="78,22 94,50 50,50" fill="#424874" />
            <polygon points="94,50 78,78 50,50" fill="#31375F" />
            <polygon points="78,78 50,94 50,50" fill="#494F7B" />
            <polygon points="50,94 22,78 50,50" fill="#5A608F" />
            <polygon points="22,78 6,50 50,50" fill="#7077A3" />
            <polygon points="6,50 22,22 50,50" fill="#9097C4" />
            <polygon points="50,38 58,50 50,62 42,50" fill="#D9DCF0" opacity="0.95" />
          </svg>
          {/* Brand Wordmark Typography */}
          <div className="flex flex-col justify-center leading-none">
            <div className="font-serif text-[22px] font-bold tracking-[0.24em] text-[#142742] uppercase">
              GUEST
            </div>
            <div className="font-serif text-[13px] font-normal tracking-[0.34em] text-[#142742] uppercase mt-0.5 flex items-center">
              <span>CONCIERGE</span>
            </div>
            <div className="text-[7.5px] font-sans font-medium tracking-[0.22em] text-[#3C5B82] uppercase mt-1.5">
              {propertyName}
            </div>
          </div>
        </div>

        {/* Divider */}
        <div className="h-8 w-[1px] bg-white/20 hidden md:block" />

        {/* Brand Motto */}
        <div className="hidden lg:flex flex-col justify-center">
          <span className="text-base lg:text-lg uppercase tracking-wider text-white font-serif font-semibold">
            {t(currentLanguage,'guestConcierge')}
          </span>
          <span className="text-xs uppercase tracking-wide text-slate-300 font-sans mt-0.5">
            {propertyName}
          </span>
        </div>
      </div>

      {/* Right: Date/Time, Languages, End Session */}
      <div className="relative z-10 flex items-center space-x-3 sm:space-x-5">
        {/* Date & Digital Clock */}
        <div className="text-right hidden sm:block">
          <div className="text-[11px] text-slate-300 font-light">{now.toLocaleDateString(currentLanguage, {weekday:'short', day:'numeric', month:'short', year:'numeric'})}</div>
          <div className="text-2xl font-light tracking-wide text-white font-sans leading-tight">
            {now.toLocaleTimeString(currentLanguage, {hour:'2-digit',minute:'2-digit'})}
          </div>
        </div>

        {/* Language Switcher Pill Group */}
        <div role="group" aria-label="Language" className="flex items-center bg-white/90 backdrop-blur-md p-1 border border-[#D9CEBC] text-[11px] font-medium shadow-sm rounded-lg">
          {languages.map(option=><button
            key={option.code}
            type="button"
            aria-pressed={currentLanguage === option.code}
            onClick={() => onLanguageChange(option.code)}
            className={`px-2.5 min-h-11 min-w-11 py-1 rounded transition ${
              currentLanguage === option.code
                ? 'bg-[#142742] text-white font-semibold shadow-sm'
                : 'text-[#1D2A3A] hover:text-[#7A5B35]'
            }`}
          >
            {option.label}
          </button>)}
        </div>

        {/* End Session Button */}
        <button
          type="button"
          onClick={onEndSession}
          className="min-h-11 flex items-center space-x-2 bg-white hover:bg-[#FAF7F2] text-[#142742] border border-white px-3.5 py-1.5 text-xs font-semibold tracking-wide transition duration-150 shadow-sm rounded-lg cursor-pointer"
        >
          <svg aria-hidden="true" focusable="false" className="w-3.5 h-3.5 text-[#142742]" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
            <path
              d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span>{t(currentLanguage,'endSession')}</span>
        </button>
      </div>
    </header>
  );
};
