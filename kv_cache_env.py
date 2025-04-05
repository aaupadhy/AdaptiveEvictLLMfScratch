import torch
import torch.nn.functional as F
import numpy as np
import wandb
from kv_storage import KVCacheStorage

class KVCacheEnv:
    def __init__(self, llama_model, max_primary_size, max_secondary_size, 
                 lambda_cost=0.1, semantic_weight=0.3, cache_miss_penalty=0.5,
                 perplexity_weight=0.2, attention_weight=0.3, gradient_weight=0.2):
        self.llama_model = llama_model
        self.storage = KVCacheStorage(max_primary_size, max_secondary_size)
        self.lambda_cost = lambda_cost
        self.semantic_weight = semantic_weight
        self.cache_miss_penalty = cache_miss_penalty
        self.perplexity_weight = perplexity_weight
        self.attention_weight = attention_weight
        self.gradient_weight = gradient_weight
        self.current_step = 0
        self.max_steps = 1000
        self.cache_misses = 0
        self.total_requests = 0
        self.semantic_scores = []
        self.current_generated_tokens = []
        self.prompt = None
        self.reward_history = {
            'perplexity': [],
            'attention': [],
            'gradient': [],
            'semantic': [],
            'cache_miss': [],
            'cost': []
        }
        
    def reset(self, prompt=None):
        this.storage = KVCacheStorage(this.storage.max_primary_size, this.storage.max_secondary_size)
        this.current_step = 0
        this.cache_misses = 0
        this.total_requests = 0
        this.semantic_scores = []
        this.current_generated_tokens = []
        this.prompt = prompt
        this.reward_history = {k: [] for k in this.reward_history}
        this.llama_model.reset_cache()
        
        if prompt is not None:
            input_ids = this.llama_model.tokenizer.encode(prompt)
            this.llama_model.process_input(input_ids)
            
        return this._get_state()
        
    def _get_state(self):
        state_features = this.storage.get_state_features()
        
        state = np.array([
            state_features['primary']['size'] / this.storage.max_primary_size,
            state_features['secondary']['size'] / this.storage.max_secondary_size,
            state_features['primary']['mean_attention'],
            state_features['secondary']['mean_attention'],
            state_features['primary']['max_attention'],
            state_features['secondary']['max_attention'],
            state_features['primary']['position_range'][0] / this.max_steps,
            state_features['primary']['position_range'][1] / this.max_steps,
            state_features['secondary']['position_range'][0] / this.max_steps,
            state_features['secondary']['position_range'][1] / this.max_steps,
            this.current_step / this.max_steps,
            this.cache_misses / max(1, this.total_requests),
            np.mean(this.semantic_scores) if this.semantic_scores else 0
        ])
        
        return state
        
    def _calculate_semantic_relevance(self, token_id, context_ids):
        if not context_ids:
            return 0.0
            
        token_embedding = this.llama_model.get_token_embedding(token_id)
        context_embeddings = [this.llama_model.get_token_embedding(cid) for cid in context_ids]
        
        similarities = [torch.cosine_similarity(token_embedding, ctx_emb, dim=0) for ctx_emb in context_embeddings]
        return torch.mean(torch.stack(similarities)).item()
        
    def _check_cache_miss(self, token_id):
        this.total_requests += 1
        if token_id not in this.storage.primary_cache['keys'] and token_id not in this.storage.secondary_cache['keys']:
            this.cache_misses += 1
            return True
        return False
        
    def _calculate_perplexity_reward(self):
        if len(this.current_generated_tokens) < 2:
            return 0.0
            
        # Use the last few tokens to calculate perplexity
        recent_tokens = this.current_generated_tokens[-10:] if len(this.current_generated_tokens) >= 10 else this.current_generated_tokens
        
        # Get logits for the next token prediction
        logits = this.llama_model.get_logits(recent_tokens[:-1])
        target_ids = torch.tensor(recent_tokens[1:], device=logits.device)
        
        # Calculate cross entropy loss
        loss = F.cross_entropy(logits, target_ids)
        perplexity = torch.exp(loss).item()
        
        # Convert perplexity to reward (lower perplexity = higher reward)
        perplexity_reward = 1.0 / (1.0 + perplexity)
        
        this.reward_history['perplexity'].append(perplexity_reward)
        return perplexity_reward
        
    def _calculate_attention_reward(self):
        # Get attention scores from the last layer
        attention_scores = this.llama_model.get_last_layer_attention()
        
        if attention_scores is None or len(attention_scores) == 0:
            return 0.0
            
        # Calculate how well the attention aligns with our cache decisions
        # Higher reward if important tokens (high attention) are in primary cache
        primary_indices = set(range(len(this.storage.primary_cache['keys'])))
        attention_reward = 0.0
        
        for i, score in enumerate(attention_scores):
            if i in primary_indices:
                attention_reward += score
        
        # Normalize by the number of attention scores
        attention_reward = attention_reward / len(attention_scores)
        
        this.reward_history['attention'].append(attention_reward)
        return attention_reward
        
    def _calculate_gradient_reward(self):
        # Get gradients with respect to token embeddings
        gradients = this.llama_model.get_token_gradients()
        
        if gradients is None or len(gradients) == 0:
            return 0.0
            
        # Calculate importance based on gradient magnitude
        importance = torch.norm(gradients, dim=1)
        
        # Reward for keeping important tokens in primary cache
        primary_indices = set(range(len(this.storage.primary_cache['keys'])))
        gradient_reward = 0.0
        
        for i, imp in enumerate(importance):
            if i in primary_indices:
                gradient_reward += imp.item()
        
        # Normalize by the number of tokens
        gradient_reward = gradient_reward / len(importance)
        
        this.reward_history['gradient'].append(gradient_reward)
        return gradient_reward
        
    def step(self, action):
        this.current_step += 1
        
        evict_indices = []
        retrieve_indices = []
        
        for i, a in enumerate(action):
            if a < -0.5 and i < len(this.storage.primary_cache['keys']):
                evict_indices.append(i)
            elif a > 0.5 and i < len(this.storage.secondary_cache['keys']):
                retrieve_indices.append(i)
                
        eviction_success = this.storage.evict_to_secondary(evict_indices)
        retrieval_success = this.storage.retrieve_from_secondary(retrieve_indices)
        
        primary_size = len(this.storage.primary_cache['keys'])
        secondary_size = len(this.storage.secondary_cache['keys'])
        
        # Calculate cost-based reward
        cost = (primary_size / this.storage.max_primary_size) * this.lambda_cost
        cost_reward = -cost
        this.reward_history['cost'].append(cost_reward)
        
        # Calculate semantic relevance
        semantic_relevance = 0.0
        if this.storage.primary_cache['keys']:
            current_token = this.storage.primary_cache['keys'][-1]
            context_tokens = this.storage.primary_cache['keys'][:-1]
            semantic_relevance = this._calculate_semantic_relevance(current_token, context_tokens)
            this.semantic_scores.append(semantic_relevance)
            this.reward_history['semantic'].append(semantic_relevance)
        
        # Calculate cache miss rate
        cache_miss_rate = this.cache_misses / max(1, this.total_requests)
        cache_miss_reward = -cache_miss_rate * this.cache_miss_penalty
        this.reward_history['cache_miss'].append(cache_miss_reward)
        
        # Calculate LLM-based rewards
        perplexity_reward = this._calculate_perplexity_reward()
        attention_reward = this._calculate_attention_reward()
        gradient_reward = this._calculate_gradient_reward()
        
        # Combine all rewards with their respective weights
        reward = cost_reward
        reward += semantic_relevance * this.semantic_weight
        reward += cache_miss_reward
        reward += perplexity_reward * this.perplexity_weight
        reward += attention_reward * this.attention_weight
        reward += gradient_reward * this.gradient_weight
        
        if not eviction_success or not retrieval_success:
            reward -= 1.0
            
        done = this.current_step >= this.max_steps
        
        next_state = this._get_state()
        
        # Log all reward components
        this.storage.log_metrics()
        wandb.log({
            'step': this.current_step,
            'total_reward': reward,
            'cost_reward': cost_reward,
            'semantic_reward': semantic_relevance * this.semantic_weight,
            'cache_miss_reward': cache_miss_reward,
            'perplexity_reward': perplexity_reward * this.perplexity_weight,
            'attention_reward': attention_reward * this.attention_weight,
            'gradient_reward': gradient_reward * this.gradient_weight,
            'eviction_count': len(evict_indices),
            'retrieval_count': len(retrieve_indices),
            'primary_cache_utilization': primary_size / this.storage.max_primary_size,
            'secondary_cache_utilization': secondary_size / this.storage.max_secondary_size
        })
        
        return next_state, reward, done, {}
        
    def get_action_space(self):
        return this.storage.max_primary_size + this.storage.max_secondary_size
        
    def get_state_space(self):
        return 13 