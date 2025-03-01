import torch
import torch.backends.cudnn as cudnn
import argparse
import numpy as np
import pandas as pd
from pyro.infer import SVI, Trace_ELBO
from torch.optim import Adam
# from pyro.optim.lr_scheduler import PyroLRScheduler
from pyro.optim import StepLR

from coma.models import init_coma
from coma.models.elbo import CustomELBO
from coma.datasets.ukbb_meshdata import (
    UKBBMeshDataset, VerticesDataLoader, get_data_from_polydata
)
from coma.utils import transforms, writer
from coma.utils.train_eval_svi import run_svi


parser = argparse.ArgumentParser(description='mesh autoencoder')
parser.add_argument('--out_dir', type=str, default='experiments')
parser.add_argument('--exp_name', type=str, default='coma')

# network hyperparameters
parser.add_argument('--model_type', default='vae_svi', type=str)
parser.add_argument('--out_channels', nargs='+', default=[32, 32, 32, 64], type=int)
parser.add_argument('--latent_channels', type=int, default=8)
parser.add_argument('--pooling_factor', type=int, default=4)
parser.add_argument('--in_channels', type=int, default=3)
parser.add_argument('--K', type=int, default=10)
parser.add_argument('--particles', type=int, default=1)
parser.add_argument('--output_particles', type=int, default=10)
parser.add_argument('--decoder_output', type=str, default='normal')
parser.add_argument('--mvn_rank', type=int, default=10)
parser.add_argument('--n_blocks', type=int, default=1)

# optimizer hyperparmeters
parser.add_argument('--lr', type=float, default=1e-3)
parser.add_argument('--lr_decay', type=float, default=1.0)

# training hyperparameters
parser.add_argument('--train_test_split', type=float, default=0.8)
parser.add_argument('--val_split', type=float, default=0.1)
parser.add_argument('--batch_size', type=int, default=10)
parser.add_argument('--epochs', type=int, default=300)
parser.add_argument('--scheduler_steps', type=int, default=50)
parser.add_argument('--step_gamma', type=float, default=0.1)

# data arguments
parser.add_argument('--substructure', type=str, default='BrStem')
parser.add_argument('--shape', type=int, default=642)
parser.add_argument(
    '--csv_path',
    type=str,
    default='/vol/biomedic3/bglocker/brainshapes/ukb21079_extracted.csv'
)

# others
parser.add_argument('--seed', type=int, default=42)

args = parser.parse_args()

# device
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# deterministic
seed = args.seed
np.random.seed(seed)
torch.manual_seed(seed)
cudnn.benchmark = False
cudnn.deterministic = True

# Preprocessor
preprocessor = transforms.get_transforms()

# Load Dataset
mesh_path = '/vol/biomedic3/bglocker/brainshapes'
cache_path = '.'
split = args.train_test_split
substructures = [args.substructure]
feature_name_map = {
    '31-0.0': 'Sex',
    '21003-0.0': 'Age',
    '25025-2.0': 'Brain Stem Volume',
}

csv_path = args.csv_path
metadata_df = pd.read_csv(csv_path)

total_train_dataset = UKBBMeshDataset(
    mesh_path,
    substructures=substructures,
    split=split,
    train=True,
    transform=preprocessor,
    reload_path=True,
    features_df=metadata_df,
    feature_name_map=feature_name_map,
    cache_path=cache_path,
)
test_dataset = UKBBMeshDataset(
    mesh_path,
    substructures=substructures,
    split=split,
    train=False,
    transform=preprocessor,
    reload_path=True,
    features_df=metadata_df,
    feature_name_map=feature_name_map,
    cache_path=cache_path,
)

val_split = args.val_split
total_train_length = len(total_train_dataset)
val_length = int(val_split * total_train_length)
train_length = total_train_length - val_length

train_dataset, val_dataset = torch.utils.data.random_split(
    total_train_dataset,
    lengths=[train_length, val_length],
    generator=torch.Generator().manual_seed(seed),
)

batch_size = args.batch_size
train_dataloader = VerticesDataLoader(
    train_dataset,
    batch_size=batch_size,
    shuffle=False,
)
val_dataloader = VerticesDataLoader(
    val_dataset,
    batch_size=10,
    shuffle=False,
)
test_dataloader = VerticesDataLoader(
    test_dataset,
    batch_size=batch_size,
    shuffle=False,
)

train_plotting_point = train_dataset.dataset.get_raw(train_dataset.indices[0])
train_data = get_data_from_polydata(train_plotting_point)
template = train_data

in_channels = 3
out_channels = args.out_channels 
latent_channels = args.latent_channels
K = args.K
n_blocks = args.n_blocks
pooling_factor = args.pooling_factor
decoder_output = args.decoder_output
model_type = args.model_type

model = init_coma(
    model_type,
    template,
    device,
    pooling_factor,
    decoder_output,
    in_channels=in_channels,
    out_channels=out_channels,
    latent_channels=latent_channels,
    K=K, n_blocks=n_blocks,
    mvn_rank=args.mvn_rank,
)
model = model.double()
print()
print(model)
print()

total_params = sum(p.numel() for p in model.parameters())
print()
print(total_params)
print()

# Sanity Check
output_particles = args.output_particles
trial_graph = torch.ones((5, args.shape, in_channels))
res = model.generate(trial_graph.to(device).double(), output_particles)
print(f'Sanity check, output shape: {res.shape}')
assert res.shape == torch.Size([5, args.shape, in_channels])

optimiser = Adam  # ({'lr': args.lr})
# scheduler = StepLR(optimiser, step_size=args.scheduler_steps, gamma=args.step_gamma)
scheduler = StepLR({
    'optimizer': optimiser,
    'optim_args': {
        'lr': args.lr,
    },
    'step_size': args.scheduler_steps,
    'gamma': args.step_gamma,
    'verbose': True,
})
loss = CustomELBO(num_particles=args.particles)
svi = SVI(model.model, model.guide, scheduler, loss=loss)
svi.loss_class = loss

# TODO: Save hyperparameters
# Save model weights

"""
GCN encoder is severely underparametrised. Linear is needed.

VAE - 50 epochs, lr = 1e-5, batch_size = 10
VAE_IAF with 3 IAFs lr = 1e-5 batch = 50 particles = 3
    Too many IAF units becomes unstable and diverges > 3
VAE - 50 epochs, lr = 1e-3, batch_size = 50 particles = 3

Next up:
VAE - 50 epochs, lr = 1e-3, batch_size = 50, with MVN decoder
"""
epochs = args.epochs
print(f'Total epochs: {epochs}')
writer = writer.MeshWriter(args, train_plotting_point)
run_svi(svi, model, train_dataloader, val_dataloader,
    epochs, scheduler, device, output_particles, writer)
