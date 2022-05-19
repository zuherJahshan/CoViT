import tensorflow as tf
import math

"""
To Remove warnings coming from model save.
as suggested in:
https://stackoverflow.com/questions/65697623/tensorflow-warning-found-untraced-functions-such-as-lstm-cell-6-layer-call-and
"""
import absl.logging
absl.logging.set_verbosity(absl.logging.ERROR)

class Linear(tf.keras.layers.Layer):
    """
    Linear layer, this layer receives as an input a matrix n*d
    and multiply it by the parameters that resides inside the weight matrix W of dimensions d*units
    after that it adds it to the bias b of size units*1.

    Long story short, it is just a layer that in the forward pass performs the following operation
    X@W + b
    The output is of course of size n*units
    """
    def __init__(self,
                 units: int = 1,    # the number of units (neurons)
                 **kwargs):
        super().__init__(**kwargs)
        self.units = units

    def build(self,
              batch_input_shape):
        self.kernel = self.add_weight(
            name="kernel",
            shape=[batch_input_shape[-1], self.units],
            initializer="glorot_normal" # TODO: check initializers
        )
        self.bias = self.add_weight(
            name="bias",
            shape=[self.units],
            initializer="zeros"
        )
        super().build(batch_input_shape)    # Call for father class to complete building

    def call(self,
             X):
        return X @ self.kernel + self.bias

    def compute_output_shape(self,
                             batch_input_shape):
        return tf.TensorShape(batch_input_shape.as_list()[:-1] +
                              [self.units])    # batch shape, n, units


    def getHP(self):
        return {"units": self.units}

    def get_config(self):
        config = super().get_config()
        config.update(self.getHP())
        return config

class ScaledDotProductAttention(tf.keras.layers.Layer):
    def __init__(self,
                 **kwargs):
        super().__init__(**kwargs)
        self.softmax = tf.keras.layers.Softmax()

    def call(self,
             K,
             V,
             Q):
        d_k = K.shape[-1]
        last_dim = len(K.shape) - 1
        # Matmul
        Z = Q @ tf.transpose(K, perm=[last_dim, last_dim-1])
        # Scale
        Z = Z / math.sqrt(d_k)
        # SoftMax
        Z = self.softmax(Z)
        # Matmul with values and return
        return Z @ V


class MyMultiHeadAttention(tf.keras.layers.Layer):
    def __init__(self,
                 d_key: int,
                 d_val: int,
                 d_model: int,
                 heads: int,
                 **kwargs):
        super().__init__(**kwargs)
        self.heads = heads
        self.lin_key_heads = [Linear(d_key) for _ in range(self.heads)]
        self.lin_qry_heads = [Linear(d_key) for _ in range(self.heads)]
        self.lin_val_heads = [Linear(d_val) for _ in range(self.heads)]
        self.scaled_dot_prod_attention = ScaledDotProductAttention()
        self.lin_out = Linear(d_model)  # Think about it

    # No need for build. No independent weights are defined in this layer. Every weight is a part of an inner layer.

    def call(self, X):

        # TODO: substitute for loop with vectorization.
        Z = []
        for i in range(self.heads):
            K = self.lin_key_heads[i](X)
            Q = self.lin_qry_heads[i](X)
            V = self.lin_val_heads[i](X)

            Z.append(self.scaled_dot_prod_attention(K=K,
                                                    V=V,
                                                    Q=Q))
        Z = tf.concat(Z, -1)
        return self.lin_out(Z) + X

    def compute_output_shape(self,
                             batch_input_shape):
        return tf.TensorShape(batch_input_shape)

    def getHP(self):
        return {"d_key": self.lin_key_heads[0].getHP()["units"],
                "d_val": self.lin_val_heads[0].getHP()["units"],
                "d_model": self.lin_out.getHP()["units"],
                "heads": self.heads}

    def get_config(self):
        config = super().get_config()
        config.update(self.getHP())
        return config


class PredictorBlock(Linear):
    """
    This class gets as an input a genome feature tensor in the form of n*d_m_model.
    this input describes n feature vectors (of size d_model) of the genome sitting horizontally.
    This layer is a dense layer with the objective of deciding which genomic class the input is from.

    This means calculates, softmax((X@W+b)[0])
    and outputs it (the dims of the output is exactly like the dims of the input).
    """
    def __init__(self,
                 **kwargs):
        super().__init__(**kwargs)

    def call(self, X):
        return tf.keras.activations.softmax((X@self.kernel + self.bias),
                                            axis=-1)    # need to be changed

    def get_config(self):
        base_config = super().get_config()
        return {**base_config}

class FeedForward(Linear):
    """
    This class gets as an input a genome in the form of n*d_m_model.
    this input describes n feature vectors (of size d_model) of the genome sitting horizontally.
    This FeedForward layer is just forwarding each feature vector (+ attention of course) through a dense layer.

    This means calculates, activation(X@W+b)
    and outputs it (the dims of the output is exactly like the dims of the input).
    """
    def __init__(self,
                 outer_units: int = 1,
                 activation: str = "relu",
                 **kwargs):
        super().__init__(**kwargs)
        self.activation = tf.keras.activations.get(activation)
        self.outer_layer = Linear(outer_units)

    def call(self, X):
        return self.outer_layer(self.activation(X@self.kernel + self.bias)) + X

    def getHP(self):
        return {"activation": tf.keras.activations.serialize(self.activation),
                "outer_units": self.outer_layer.getHP()["units"]}

    def get_config(self):
        config = super().get_config()
        config.update(self.getHP())
        return config


class EncoderBlock(tf.keras.layers.Layer):
    def __init__(self,
                 d_model: int = 1,
                 d_val: int = 1,
                 d_key: int = 1,
                 d_ff: int = 1,
                 heads: int = 1,
                 **kwargs):
        super().__init__(**kwargs)
        self.mha = MyMultiHeadAttention(d_val=d_val,
                                      d_key=d_key,
                                      d_model=d_model,
                                      heads=heads)
        self.ff = FeedForward(outer_units=d_model,
                              units=d_ff)  # TODO: Maybe add gelu as the activation following ViT
        self.norm = tf.keras.layers.Normalization()

    def call(self,
             X):
        Z = self.norm(X)
        Z = self.mha(Z)
        Z = self.norm(Z)
        Z = self.ff(Z)
        return Z

    def getHP(self):
        HP = self.mha.getHP()
        HP.update(self.ff.getHP())
        return HP

    def get_config(self):
        config = super().get_config()
        config.update(self.getHP())
        return config

class CoViTModel(tf.keras.Model):
    def __init__(self,
                 N: int = 1,    # Number of repeats of the EncoderBlock
                 d_out: int = 2,    # The number of classes
                 d_model: int = 1,
                 d_val: int = 1,
                 d_key: int = 1,
                 d_ff: int = 1,
                 heads: int = 1,
                 **kwargs):
        super().__init__(**kwargs)
        self.N = N
        self.base_embedding = Linear(units=1)
        self.encoder_blocks = [EncoderBlock(d_model=d_model,
                                            d_val=d_val,
                                            d_key=d_key,
                                            d_ff=d_ff,
                                            heads=heads) for _ in range(self.N)]
        self.norm = tf.keras.layers.Normalization()
        self.out = PredictorBlock(units=d_out)

    def call(self, X):
        Z = self.base_embedding(X)   # X of size [batch, n, d_model, 4]
        for encoder_block in self.encoder_blocks:
            Z = encoder_block(Z)
        Z = self.norm(Z)
        return self.out(Z)

    def getHP(self):
        HP = {"N": self.N}
        HP.update(self.encoder_blocks[0].getHP())
        HP.update(self.out.getHP())
        return HP

    def get_config(self):
        config = super().get_config()
        config.update(self.getHP())
        return config

custom_objects = {"Linear": Linear,
                  "ScaledDotProductAttention": ScaledDotProductAttention,
                  "MyMultiHeadAttention": MyMultiHeadAttention,
                  "PredictorBlock": PredictorBlock,
                  "FeedForward": FeedForward,
                  "EncoderBlock": EncoderBlock,
                  "CoViTModel": CoViTModel}






"""
TODO: 
    1. Fix get_config's and try saving the model to see that it works. Done
    2. Try to include GPU in calculations
    3. Parallelize MultiHeadAttention loop.
    4. Work on training loop.
"""
