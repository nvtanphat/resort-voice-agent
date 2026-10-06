import type {ComponentProps} from 'react';
import {ChatSection} from '../components/ChatSection';

/** Screen boundary for the guest conversation and read-only request preview. */
export function Chat(props:ComponentProps<typeof ChatSection>){
 return <ChatSection {...props}/>;
}
