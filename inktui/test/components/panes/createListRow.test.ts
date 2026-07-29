import { describe, expect, it, vi } from 'vitest';
import {
  dataIndexFromCursor,
  isCreateCursor,
  listRowCountWithCreate,
  onEnterCreateOrOpen,
} from '../../../src/components/panes/shared/createListRow.js';

describe('createListRow helpers', () => {
  it('treats cursor 0 as the create row', () => {
    expect(isCreateCursor(0)).toBe(true);
    expect(isCreateCursor(1)).toBe(false);
  });

  it('maps cursor to data index after the create row', () => {
    expect(dataIndexFromCursor(0)).toBeNull();
    expect(dataIndexFromCursor(1)).toBe(0);
    expect(dataIndexFromCursor(3)).toBe(2);
  });

  it('counts the create row in list length', () => {
    expect(listRowCountWithCreate(0)).toBe(1);
    expect(listRowCountWithCreate(4)).toBe(5);
  });

  it('routes enter to create or open', () => {
    const onCreate = vi.fn();
    const onOpen = vi.fn();
    onEnterCreateOrOpen(0, onCreate, onOpen);
    expect(onCreate).toHaveBeenCalledOnce();
    expect(onOpen).not.toHaveBeenCalled();
    onEnterCreateOrOpen(2, onCreate, onOpen);
    expect(onOpen).toHaveBeenCalledWith(1);
  });
});
