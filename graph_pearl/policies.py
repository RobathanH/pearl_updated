import numpy as np
import torch
from torch import nn as nn
import torch_geometric as gtorch

from rlkit.core.util import Wrapper
from rlkit.policies.base import ExplorationPolicy, Policy
from rlkit.torch.distributions import TanhNormal
from rlkit.torch.core import PyTorchModule
from rlkit.torch.core import np_ify

from .graph_utils import Node
from .graph_modules import GraphTransformer

LOG_SIG_MAX = 2
LOG_SIG_MIN = -20


class Graph_TanhGaussianPolicy(PyTorchModule, ExplorationPolicy):
    """
    Usage:

    ```
    policy = TanhGaussianPolicy(...)
    action, mean, log_std, _ = policy(obs, z)
    action, mean, log_std, _ = policy(obs, z, deterministic=True)
    action, mean, log_std, log_prob = policy(obs, z, return_log_prob=True)
    ```
    Here, mean and log_std are the mean and log_std of the Gaussian that is
    sampled from.

    If deterministic is True, action = tanh(mean).
    If return_log_prob is False (default), log_prob = None
        This is done because computing the log_prob can be a bit expensive.
    """

    def __init__(
            self,
            inner_node_dim: int,
            inner_node_edges: int,
            conv_iterations: int,
            state_dim: int,
            latent_dim: int,
            action_dim: int
    ):
        self.save_init_params(locals())
        super().__init__()
        
        self.inner_node_dim = inner_node_dim
        self.inner_node_edges = inner_node_edges
        self.conv_iterations = conv_iterations
        self.state_dim = state_dim
        self.latent_dim = latent_dim
        self.action_dim = action_dim
        
        self.module = GraphTransformer(
            {
                Node.STATE_IN: state_dim,
                Node.LATENT_IN: latent_dim
            },
            inner_node_dim,
            inner_node_edges,
            2 * action_dim,
            conv_iterations
        )

    def get_action(self, graph: gtorch.data.HeteroData, deterministic: bool = False):
        actions = self.get_actions(graph, deterministic=deterministic)
        return actions[0, :], {}

    @torch.no_grad()
    def get_actions(self, graph: gtorch.data.HeteroData, deterministic: bool = False):
        outputs = self.forward(graph, deterministic=deterministic)[0]
        return np_ify(outputs)

    def forward(
            self,
            graph: gtorch.data.HeteroData,
            reparameterize: bool = False,
            deterministic: bool = False,
            return_log_prob: bool = False,
    ):
        """
        :param obs: Observation
        :param deterministic: If True, do not sample
        :param return_log_prob: If True, return a sample and its log probability
        """
        mean, log_std = torch.tensor_split(self.module(graph), 2, dim=-1)
        log_std = torch.clamp(log_std, LOG_SIG_MIN, LOG_SIG_MAX)
        std = torch.exp(log_std)

        log_prob = None
        expected_log_prob = None
        mean_action_log_prob = None
        pre_tanh_value = None
        if deterministic:
            action = torch.tanh(mean)
        else:
            tanh_normal = TanhNormal(mean, std)
            if return_log_prob:
                if reparameterize:
                    action, pre_tanh_value = tanh_normal.rsample(
                        return_pretanh_value=True
                    )
                else:
                    action, pre_tanh_value = tanh_normal.sample(
                        return_pretanh_value=True
                    )
                log_prob = tanh_normal.log_prob(
                    action,
                    pre_tanh_value=pre_tanh_value
                )
                log_prob = log_prob.sum(dim=-1, keepdim=True)
            else:
                if reparameterize:
                    action = tanh_normal.rsample()
                else:
                    action = tanh_normal.sample()

        return (
            action, mean, log_std, log_prob, expected_log_prob, std,
            mean_action_log_prob, pre_tanh_value,
        )


class MakeDeterministic(Wrapper, Policy):
    def __init__(self, stochastic_policy):
        super().__init__(stochastic_policy)
        self.stochastic_policy = stochastic_policy

    def get_action(self, observation):
        return self.stochastic_policy.get_action(observation,
                                                 deterministic=True)

    def get_actions(self, observations):
        return self.stochastic_policy.get_actions(observations,
                                                  deterministic=True)
