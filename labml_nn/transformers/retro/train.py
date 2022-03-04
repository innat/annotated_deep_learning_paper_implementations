import torch
from torch import nn
from torch.utils.data import DataLoader

from labml import monit, lab, tracker, experiment, logger
from labml.logger import Text
from labml_helpers.datasets.text import TextFileDataset
from labml_nn.optimizers.noam import Noam
from labml_nn.transformers.retro import model as retro
from labml_nn.transformers.retro.dataset import Dataset, RetroIndex
from labml_nn.transformers.retro.model import RetroModel, Encoder


class Sampler:
    def __init__(self, device: torch.device, model: retro.RetroModel, tds: TextFileDataset, chunk_len: int):
        self.chunk_len = chunk_len
        self.tds = tds
        self.model = model
        self.device = device
        self.index = RetroIndex()

    def get_neighbours(self, chunk: str):
        neighbor_offsets = self.index([chunk], None)
        text = self.tds.train

        neighbors = [text[j: j + self.chunk_len * 2] for j in neighbor_offsets[0]]

        return neighbors

    def sample(self, prompt: str, sample_len):
        neighbors_str = []
        sampled = ''
        # prompt = self.tds.text_to_i(prompt)
        for i in range(sample_len):
            while len(neighbors_str) < len(prompt) // self.chunk_len:
                off = len(neighbors_str) * self.chunk_len
                chunk = prompt[off: off + self.chunk_len]
                neighbors_str.append(self.get_neighbours(chunk))

            src = self.tds.text_to_i(prompt)
            neighbors = torch.stack([torch.stack([self.tds.text_to_i(n) for n in chunk]) for chunk in neighbors_str])

            src = src.to(self.device)
            neighbors = neighbors.to(self.device)

            res = self.model(src[None, :], neighbors[None, :, :, :])

            token = res[0, -1, :].argmax(dim=-1)

            prompt += self.tds.itos[token.item()]

            sampled += self.tds.itos[token.item()]

        return sampled


class Trainer:
    epochs: int = 200
    batch_size: int = 1

    learning_rate = 0.0002
    adam_betas = (0.5, 0.999)
    decay_start = 100

    def __init__(self, device: torch.device, model: retro.RetroModel, dataloader: DataLoader,
                 optimizer: torch.optim.Adam):
        self.optimizer = optimizer
        self.device = device
        self.dataloader = dataloader
        self.model = model
        self.loss_func = nn.CrossEntropyLoss()

    def __call__(self):
        for i, (src, tgt, neighbors) in monit.enum('Train', self.dataloader):
            # Move images to the device
            src, tgt, neighbors = src.to(self.device), tgt.to(self.device), neighbors.to(self.device)

            res = self.model(src, neighbors)
            loss = self.loss_func(res.view(-1, res.shape[-1]), tgt.view(-1))

            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            # Save training statistics and increment the global step counter
            tracker.save({'loss.train': loss})
            tracker.add_global_step(len(src))


def train():
    experiment.create(name='retro_small')

    device = torch.device('cuda:0')
    tds = TextFileDataset(
        lab.get_data_path() / 'tiny_shakespeare.txt',
        list,
        url='https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt')

    train_dataset = Dataset(lab.get_data_path() / 'retro_train_dataset.json', tds)
    train_dl = DataLoader(train_dataset, batch_size=4, shuffle=True)

    chunk_length = 64
    d_model = 512
    d_ff = 2048
    n_heads = 16
    d_k = 32
    model = RetroModel(tds.n_tokens, d_model, 6, {2, 5}, chunk_length, n_heads, d_k, d_ff,
                       encoder=Encoder(chunk_length, 3, {2}, d_model, n_heads, d_k, d_ff))

    model = model.to(device)

    optimizer = Noam(model.parameters(), lr=1., d_model=d_model, warmup=2_000)

    trainer = Trainer(device, model, train_dl, optimizer)

    sampler = Sampler(device, model, tds, chunk_length)

    prompt = '''First Citizen:
We are accounted poor citizens, the patricians good.
What authority surfeits on would relieve us: if they
would yield us but the superfluity, while it were
wholesome, we might guess they relieved us humanely;
but they think we are too dear: the leanness that
afflicts us, the object of our misery, is as an
inventory to particularise their abundance; our
sufferance is a gain to them Let us revenge this with
our pikes, ere we become rakes: for the gods know I
speak this in hunger for bread, not in '''

    experiment.add_pytorch_models(model=model)

    # experiment.load('711813509ba211ecb9bcbf7bc5fb18d4')
    with experiment.start():
        logger.log([(prompt.replace('\n', '\\n\n'), Text.subtle),
                    (sampler.sample(prompt, 128).replace('\n', '\\n\n'), Text.none)])

        for epoch in monit.loop(32):
            trainer()
            tracker.new_line()
            logger.log([(prompt[-10:].replace('\n', '\\n\n'), Text.subtle),
                        (sampler.sample(prompt, 128).replace('\n', '\\n\n'), Text.none)])
            experiment.save_checkpoint()


def sample():
    device = torch.device('cuda:0')
    tds = TextFileDataset(
        lab.get_data_path() / 'tiny_shakespeare.txt',
        list,
        url='https://raw.githubusercontent.com/karpathy/char-rnn/master/data/tinyshakespeare/input.txt')

    chunk_length = 64
    d_model = 512
    d_ff = 2048
    n_heads = 16
    d_k = 32
    model = RetroModel(tds.n_tokens, d_model, 6, {2, 5}, chunk_length, n_heads, d_k, d_ff,
                       encoder=Encoder(chunk_length, 3, {2}, d_model, n_heads, d_k, d_ff))

    model = model.to(device)

    sampler = Sampler(device, model, tds, chunk_length)

    prompt = '''First Citizen:
We are accounted poor citizens, the patricians good.
What authority surfeits on would relieve us: if they
would yield us but the superfluity, while it were
wholesome, we might guess they relieved us humanely;
but they think we are too dear: the leanness that
afflicts us, the object of our misery, is as an
inventory to particularise their abundance; our
sufferance is a gain to them Let us revenge this with
our pikes, ere we become rakes: for the gods know I
speak this in hunger for bread, not in thirst for revenge.
'''
    sampler.sample(prompt, 10)


if __name__ == '__main__':
    train()
    # sample()
