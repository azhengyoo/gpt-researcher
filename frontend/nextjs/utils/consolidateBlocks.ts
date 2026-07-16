export const consolidateSourceAndImageBlocks = (groupedData: any[]) => {
  // Consolidate sourceBlocks with O(n) dedup using Map
  const sourceItems: any[] = [];
  const sourceItemMap = new Map<string, any>();

  for (const item of groupedData) {
    if (item.type === 'sourceBlock' && item.items) {
      for (const sourceItem of item.items) {
        if (sourceItem.url && !sourceItemMap.has(sourceItem.url)) {
          sourceItemMap.set(sourceItem.url, sourceItem);
          sourceItems.push(sourceItem);
        }
      }
    }
  }

  const consolidatedSourceBlock = {
    type: 'sourceBlock',
    items: sourceItems,
  };

  // Consolidate imageBlocks
  const imageMetadata: any[] = [];
  for (const item of groupedData) {
    if (item.type === 'imagesBlock' && item.metadata) {
      for (const meta of item.metadata) {
        imageMetadata.push(meta);
      }
    }
  }

  const consolidatedImageBlock = {
    type: 'imagesBlock',
    metadata: imageMetadata,
  };

  // Remove all existing sourceBlocks and imageBlocks — collect non-source/image in one pass
  const filtered: any[] = [];
  for (const item of groupedData) {
    if (item.type !== 'sourceBlock' && item.type !== 'imagesBlock') {
      filtered.push(item);
    }
  }

  // Add consolidated blocks if they have items
  if (consolidatedSourceBlock.items.length > 0) {
    filtered.push(consolidatedSourceBlock);
  }
  if (consolidatedImageBlock.metadata.length > 0) {
    filtered.push(consolidatedImageBlock);
  }

  return filtered;
};