/** Guest UI copy comes from the same locale files as the backend. */
import type {LanguageCode,RequestStatus,TaskProgress} from './api';
import viLocale from '../../locales/vi.json';
import enLocale from '../../locales/en.json';
import zhLocale from '../../locales/zh.json';
import koLocale from '../../locales/ko.json';

type Locale = Record<string, string>;
const toUiCopy = (locale: Record<string, string>): Locale => Object.fromEntries(
 Object.entries(locale).filter(([key])=>key.startsWith('ui.')).map(([key,value])=>[key.slice(3),value])
);
const copy: Record<LanguageCode, Locale> = {
 vi: toUiCopy(viLocale), en: toUiCopy(enLocale), zh: toUiCopy(zhLocale), ko: toUiCopy(koLocale),
};
const BCP47: Record<LanguageCode,string> = {vi:'vi-VN',en:'en-GB',zh:'zh-CN',ko:'ko-KR'};
export const localeFor=(language:LanguageCode):string=>BCP47[language]||'en-GB';
export type CopyKey = keyof typeof copy.en;
export const t=(language:LanguageCode,key:CopyKey):string=>copy[language][key];
export const statusLabel=(language:LanguageCode,status:RequestStatus)=>t(language,status);
export const taskLabel=(language:LanguageCode,task:'knowledge'|'navigation'|'planning'|TaskProgress['status'])=>t(language,task);
