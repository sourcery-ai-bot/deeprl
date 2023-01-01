from pathlib import Path
from datetime import datetime
from functools import partial

import gymnasium as gym
import numpy as np
import torch
import torch.optim as optim
import hydra
from gymnasium.experimental.wrappers import NumpyToTorchV0
from torch.utils.tensorboard import SummaryWriter
from omegaconf import DictConfig

from conf import EnvConfig, TD3Config
from deeprl.actor_critic_methods import TD3
from deeprl.actor_critic_methods.neural_network.mlp import Actor, Critic
from deeprl.actor_critic_methods.experience_replay import UER
from deeprl.actor_critic_methods.noise_injection.action_space import Gaussian


@hydra.main(version_base=None, config_path='conf', config_name='train_td3')
def train(cfg: DictConfig) -> None:
    env_cfg = EnvConfig(**cfg['env'])
    td3_cfg = TD3Config(**cfg['td3'])

    device = torch.device(env_cfg.device)

    env = gym.make(env_cfg.gym_name)
    if isinstance(env.observation_space.dtype, np.dtype):  # is a Numpy-based env
        # Precision alignment with the env.
        if env.observation_space.dtype == np.float64:
            torch.set_default_dtype(torch.float64)  # Python floats are interpreted as float64
        elif env.observation_space.dtype == np.float32:
            torch.set_default_dtype(torch.float32)  # Python floats are interpreted as float32
            pass  # The default floating point dtype is initially torch.float32
        else:
            raise TypeError(f"Unexpected {type(env.observation_space.dtype)} data type of an observation space.")

        env = NumpyToTorchV0(env, device=device)  # converts Numpy-based env to PyTorch-based

    agent = TD3(
        device,
        env.observation_space.shape[0],
        env.action_space.shape[0],
        partial(Actor, hidden_dims=td3_cfg.hidden_dims, activation_fn='relu', output_fn='tanh'),
        partial(Critic, hidden_dims=td3_cfg.hidden_dims, activation_fn='relu'),
        partial(optim.Adam, lr=td3_cfg.actor_lr , weight_decay=td3_cfg.weight_decay),
        partial(optim.Adam, lr=td3_cfg.critic_lr, weight_decay=td3_cfg.weight_decay),
        UER(td3_cfg.memory_capacity),
        td3_cfg.batch_size,
        td3_cfg.discount_factor,
        td3_cfg.polyak,
        Gaussian(td3_cfg.action_noise_stddev, td3_cfg.action_noise_decay_const),
        td3_cfg.clip_bound,
        td3_cfg.stddev,
    )

    checkpoint_dir = Path(__file__).resolve().parent/'.checkpoints'/'TD3'/f'{env.spec.name}-v{env.spec.version}'/f'{datetime.now().strftime("%Y%m%d%H%M")}'
    with SummaryWriter(log_dir=Path(__file__).resolve().parent/'.logs'/'TD3'/f'{env.spec.name}-v{env.spec.version}'/f'{datetime.now().strftime("%Y%m%d%H%M")}') as writer:
        for episode in range(env_cfg.num_episodes):
            state, _ = env.reset()
            episodic_return = torch.zeros(1, device=device)

            while True:
                action = agent.compute_action(state)

                next_state, reward, terminated, truncated, _ = env.step(action.cpu())  # Perform an action

                episodic_return += reward
                # Convert to size(1,) tensor
                reward = torch.tensor([reward], device=device)
                terminated = torch.tensor([terminated], device=device, dtype=torch.bool)

                # Store a transition in the experience replay and perform one step of the optimisation
                agent.step(state, action, reward, next_state, terminated)

                if terminated or truncated:
                    break
                state = next_state  # Move to the next state

            # Logging
            # TODO: Plot mean ± stddev curve for selecting the best model
            writer.add_scalar(f'{env.spec.name}-v{env.spec.version}/episodic_return', episodic_return.item(), episode)

            # Periodical checkpointing
            if episode % 20 == 0:
                checkpoint_dir.mkdir(parents=True, exist_ok=True)
                policy_scripted = torch.jit.script(agent._policy)
                policy_scripted.save(checkpoint_dir/f'ep{episode}.pt')


if __name__ == '__main__':
    train()
