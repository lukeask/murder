/** @deprecated Prefer `TextEditorDisplay` with an owned `TextEditorState`. */
import type { JSX } from 'react';
import { editorAtEnd } from '../input/textEditor/state.js';
import { TextEditorDisplay } from './TextEditorDisplay.js';

export interface TextInputProps {
  readonly value: string;
  readonly placeholder?: string;
  readonly focused?: boolean;
  readonly color?: string;
  /** Supplied by migrated callers from their actual content allocation. */
  readonly width?: number;
}

/** Compatibility presentation wrapper; editing belongs to the owner and shared reducer. */
export function TextInput({
  value,
  placeholder,
  focused = false,
  color,
  width = 80,
}: TextInputProps): JSX.Element {
  return (
    <TextEditorDisplay
      state={editorAtEnd(value)}
      width={width}
      {...(placeholder === undefined ? {} : { placeholder })}
      focused={focused}
      {...(color === undefined ? {} : { color })}
    />
  );
}
