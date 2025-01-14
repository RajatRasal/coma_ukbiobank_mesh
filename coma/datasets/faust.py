import random
import shutil
import os.path as osp
from typing import Optional, Callable, List, Tuple

import torch
from torch_geometric.datasets import FAUST
from torch.utils.data import DataLoader
from torch_geometric.data import InMemoryDataset, Data, extract_zip
from torch_geometric.io import read_ply


class FullFAUST(InMemoryDataset):
    """
    Adapted from Pytorch Geometric FAUST dataloader
    https://pytorch-geometric.readthedocs.io/en/latest/_modules/torch_geometric/datasets/faust.html#FAUST
    """

    url = 'http://faust.is.tue.mpg.de/'

    def __init__(self, root: str, train: bool = True,
                 transform: Optional[Callable] = None,
                 pre_transform: Optional[Callable] = None,
                 pre_filter: Optional[Callable] = None):
        super().__init__(root, transform, pre_transform, pre_filter)
        # path = self.processed_paths[0] if train else self.processed_paths[1]
        self.data, self.slices = torch.load(self.processed_paths[0])

    @property
    def raw_file_names(self) -> str:
        return 'MPI-FAUST.zip'

    @property
    def processed_file_names(self) -> List[str]:
        return ['all_faust.pt']

    def download(self):
        raise RuntimeError(
            f"Dataset not found. Please download '{self.raw_file_names}' from "
            f"'{self.url}' and move it to '{self.raw_dir}'")

    def process(self):
        extract_zip(self.raw_paths[0], self.raw_dir, log=False)

        path = osp.join(self.raw_dir, 'MPI-FAUST', 'training', 'registrations')
        path = osp.join(path, 'tr_reg_{0:03d}.ply')

        data_list = []

        for i in range(100):
            data = read_ply(path.format(i))
            data.person = torch.tensor([i % 10], dtype=torch.long)
            data.pose = torch.tensor([i // 10], dtype=torch.long)
            if self.pre_filter is not None and not self.pre_filter(data):
                continue
            if self.pre_transform is not None:
                data = self.pre_transform(data)
            data_list.append(data)

        torch.save(self.collate(data_list), self.processed_paths[0])
        shutil.rmtree(osp.join(self.raw_dir, 'MPI-FAUST'))

def split_faust_by_person(dataset: FullFAUST, test_people_ids: List[int]) -> Tuple[List[Data], List[Data]]:
    train = []
    test = []
    for data in dataset:
        if data.person in test_people_ids:
            test.append(data)
        else:
            train.append(data)
    return train, test


# TODO: Refactor BatchWrapper
from collections import namedtuple
BatchWrapper = namedtuple('BatchWrapper', ['x', 'pose'])

class FAUSTDataLoader(DataLoader):

    def __init__(self, dataset: FAUST, batch_size=1, shuffle=False, onehot=False, **kwargs):

        def collate_fn(data_list: List[Data]):
            batch = torch.vstack([data.pos for data in data_list])
            batch = batch.reshape(-1, *data_list[0].pos.shape).double()
            pose = torch.vstack([data.pose for data in data_list])
            if onehot:
                pose = torch.nn.functional.one_hot(pose.flatten(), num_classes=10)
            else:
                pose = pose.reshape(-1, *data_list[0].pose.shape).double()
            return BatchWrapper(x=batch, pose=pose)

        super(FAUSTDataLoader, self).__init__(
            dataset,
            batch_size,
            shuffle,
            collate_fn=collate_fn,
            **kwargs,
        )


if __name__ == '__main__':
    d = FullFAUST('.')
    loader = FAUSTDataLoader(d, 5, onehot=True, shuffle=True) 
    for x in loader:
        print(x)
