# -*- coding: utf-8 -*-
import numpy as np
import chainer, os, collections, six
from chainer import cuda, Variable, optimizers, serializers
from chainer import functions as F
from chainer import links as L

activations = {
	"sigmoid": F.sigmoid, 
	"tanh": F.tanh, 
	"softplus": F.softplus, 
	"relu": F.relu, 
	"leaky_relu": F.leaky_relu, 
	"elu": F.elu
}

class Conf():
	def __init__(self):
		self.image_width = 28
		self.image_height = 28
		self.ndim_x = 28 * 28
		self.ndim_z = 100

		# ie.
		# 768(input vector) -> 2000 -> 1000 -> 100(output vector)
		# encoder_units = [768, 2000, 1000, 100]
		self.encoder_units = [self.ndim_x, 2000, 1000, self.ndim_z]
		self.encoder_activation_function = "tanh"
		self.encoder_output_activation_function = None	# 指定しないほうがp(z)に押し込めている感を味わえる
		self.encoder_apply_dropout = True
		self.encoder_apply_batchnorm = False
		self.encoder_apply_batchnorm_to_input = False

		self.decoder_units = [self.ndim_z, 2000, 1000, self.ndim_x]
		self.decoder_activation_function = "tanh"
		self.decoder_output_activation_function = None	# this will be ignored when decoder is BernoulliDecoder
		self.decoder_apply_dropout = True
		self.decoder_apply_batchnorm = False
		self.decoder_apply_batchnorm_to_input = False

		self.use_gpu = True
		self.learning_rate = 0.00025
		self.gradient_momentum = 0.95

def sum_sqnorm(arr):
	sq_sum = collections.defaultdict(float)
	for x in arr:
		with cuda.get_device(x) as dev:
			x = x.ravel()
			s = x.dot(x)
			sq_sum[int(dev)] += s
	return sum([float(i) for i in six.itervalues(sq_sum)])
	
class GradientClipping(object):
	name = "GradientClipping"

	def __init__(self, threshold):
		self.threshold = threshold

	def __call__(self, opt):
		norm = np.sqrt(sum_sqnorm([p.grad for p in opt.target.params()]))
		if norm == 0:
			norm = 1
		rate = self.threshold / norm
		if rate < 1:
			for param in opt.target.params():
				grad = param.grad
				with cuda.get_device(grad):
					grad *= rate

class VAE():
	# name is used for the filename when you save the model
	def __init__(self, conf, name="vae"):
		self.encoder, self.decoder = self.build(conf)
		self.name = name

		self.optimizer_encoder = optimizers.Adam(alpha=conf.learning_rate, beta1=conf.gradient_momentum)
		self.optimizer_encoder.setup(self.encoder)
		self.optimizer_encoder.add_hook(GradientClipping(10.0))

		self.optimizer_decoder = optimizers.Adam(alpha=conf.learning_rate, beta1=conf.gradient_momentum)
		self.optimizer_decoder.setup(self.decoder)
		self.optimizer_decoder.add_hook(GradientClipping(10.0))

	def build(self, conf):
		raise Exception()

	def train(self, x, L=1, test=False):
		raise Exception()

	@property
	def xp(self):
		return self.encoder.xp

	@property
	def gpu(self):
		if cuda.available is False:
			return False
		return True if self.xp is cuda.cupy else False

	def zero_grads(self):
		self.optimizer_encoder.zero_grads()
		self.optimizer_decoder.zero_grads()

	def update(self):
		self.optimizer_encoder.update()
		self.optimizer_decoder.update()

	def encode(self, x, test=False):
		return self.encoder(x, test=test)

	def decode(self, z, test=False, output_pixel_value=True):
		return self.decoder(z, test=test, output_pixel_value=output_pixel_value)

	def __call__(self, x, test=False):
		return self.decoder(self.encoder(x, test=test), test=test)

	def load(self, dir=None):
		if dir is None:
			raise Exception()
		for attr in vars(self):
			prop = getattr(self, attr)
			if isinstance(prop, chainer.Chain) or isinstance(prop, chainer.optimizer.GradientMethod):
				filename = dir + "/%s_%s.hdf5" % (self.name, attr)
				if os.path.isfile(filename):
					serializers.load_hdf5(filename, prop)
				else:
					print filename, "missing."
		print "model loaded."

	def save(self, dir=None):
		if dir is None:
			raise Exception()
		try:
			os.mkdir(dir)
		except:
			pass
		for attr in vars(self):
			prop = getattr(self, attr)
			if isinstance(prop, chainer.Chain) or isinstance(prop, chainer.optimizer.GradientMethod):
				serializers.save_hdf5(dir + "/%s_%s.hdf5" % (self.name, attr), prop)
		print "model saved."

class GaussianVAE(VAE):

	def build(self, conf):
		wscale = 0.1
		encoder_attributes = {}
		encoder_units = zip(conf.encoder_units[:-1], conf.encoder_units[1:])
		for i, (n_in, n_out) in enumerate(encoder_units):
			encoder_attributes["layer_mean_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_attributes["batchnorm_mean_%i" % i] = L.BatchNormalization(n_in)
			encoder_attributes["layer_var_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_attributes["batchnorm_var_%i" % i] = L.BatchNormalization(n_in)
		encoder = Encoder(**encoder_attributes)
		encoder.n_layers = len(encoder_units)
		encoder.activation_function = conf.encoder_activation_function
		encoder.output_activation_function = conf.encoder_output_activation_function
		encoder.apply_dropout = conf.encoder_apply_dropout
		encoder.apply_batchnorm = conf.encoder_apply_batchnorm
		encoder.apply_batchnorm_to_input = conf.encoder_apply_batchnorm_to_input

		decoder_attributes = {}
		decoder_units = zip(conf.decoder_units[:-1], conf.decoder_units[1:])
		for i, (n_in, n_out) in enumerate(decoder_units):
			decoder_attributes["layer_mean_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			decoder_attributes["batchnorm_mean_%i" % i] = L.BatchNormalization(n_in)
			decoder_attributes["layer_var_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			decoder_attributes["batchnorm_var_%i" % i] = L.BatchNormalization(n_in)
		decoder = GaussianDecoder(**decoder_attributes)
		decoder.n_layers = len(decoder_units)
		decoder.activation_function = conf.decoder_activation_function
		decoder.output_activation_function = conf.decoder_output_activation_function
		decoder.apply_dropout = conf.decoder_apply_dropout
		decoder.apply_batchnorm = conf.decoder_apply_batchnorm
		decoder.apply_batchnorm_to_input = conf.decoder_apply_batchnorm_to_input

		if conf.use_gpu:
			encoder.to_gpu()
			decoder.to_gpu()
		return encoder, decoder

	def train(self, x, L=1, test=False):
		z_mean, z_ln_var = self.encoder(x, test=test, sample_output=False)
		reconstuction_loss = 0
		for l in xrange(L):
			# Sample z
			z = F.gaussian(z_mean, z_ln_var)
			# Decode
			x_reconstruction_mean, x_reconstruction_ln_var = self.decoder(z, test=test, output_pixel_value=False)
			# Approximation of E_q(z|x)[log(p(x|z))]
			reconstuction_loss += F.gaussian_nll(x, x_reconstruction_mean, x_reconstruction_ln_var)
		loss = reconstuction_loss / (L * x.data.shape[0])
		# KL divergence
		kld_regularization_loss = F.gaussian_kl_divergence(z_mean, z_ln_var)
		loss += kld_regularization_loss / x.data.shape[0]

		self.zero_grads()
		loss.backward()
		self.update()

		if self.gpu:
			loss.to_cpu()
		return loss.data

class BernoulliVAE(VAE):

	def build(self, conf):
		wscale = 0.1
		encoder_attributes = {}
		encoder_units = zip(conf.encoder_units[:-1], conf.encoder_units[1:])
		for i, (n_in, n_out) in enumerate(encoder_units):
			encoder_attributes["layer_mean_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_attributes["batchnorm_mean_%i" % i] = L.BatchNormalization(n_in)
			encoder_attributes["layer_var_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			encoder_attributes["batchnorm_var_%i" % i] = L.BatchNormalization(n_in)
		encoder = Encoder(**encoder_attributes)
		encoder.n_layers = len(encoder_units)
		encoder.activation_function = conf.encoder_activation_function
		encoder.output_activation_function = conf.encoder_output_activation_function
		encoder.apply_dropout = conf.encoder_apply_dropout
		encoder.apply_batchnorm = conf.encoder_apply_batchnorm
		encoder.apply_batchnorm_to_input = conf.encoder_apply_batchnorm_to_input

		decoder_attributes = {}
		decoder_units = zip(conf.decoder_units[:-1], conf.decoder_units[1:])
		for i, (n_in, n_out) in enumerate(decoder_units):
			decoder_attributes["layer_%i" % i] = L.Linear(n_in, n_out, wscale=wscale)
			decoder_attributes["batchnorm_%i" % i] = L.BatchNormalization(n_in)
		decoder = BernoulliDecoder(**decoder_attributes)
		decoder.n_layers = len(decoder_units)
		decoder.activation_function = conf.decoder_activation_function
		decoder.output_activation_function = conf.decoder_output_activation_function
		decoder.apply_dropout = conf.decoder_apply_dropout
		decoder.apply_batchnorm = conf.decoder_apply_batchnorm
		decoder.apply_batchnorm_to_input = conf.decoder_apply_batchnorm_to_input

		if conf.use_gpu:
			encoder.to_gpu()
			decoder.to_gpu()
		return encoder, decoder

	def decode(self, z, test=False, output_pixel_value=False):
		return self.decoder(z, test=test, output_pixel_value=output_pixel_value)

	def train(self, x, L=1, test=False):
		z_mean, z_ln_var = self.encoder(x, test=test, sample_output=False)
		loss = 0
		for l in xrange(L):
			# Sample z
			z = F.gaussian(z_mean, z_ln_var)
			# Decode
			x_expectation = self.decoder(z, test=test)
			# Approximation of E_q(z|x)[log(p(x|z))]
			# x is between -1 to 1 so we convert it to be between 0 to 1
			reconstuction_loss = F.bernoulli_nll((x + 1.0) / 2.0, x_expectation)
			loss += reconstuction_loss
		loss /= L * x.data.shape[0]
		# KL divergence
		kld_regularization_loss = F.gaussian_kl_divergence(z_mean, z_ln_var)
		loss += kld_regularization_loss / x.data.shape[0]

		self.zero_grads()
		loss.backward()
		self.update()

		if self.gpu:
			loss.to_cpu()
		return loss.data

class Encoder(chainer.Chain):
	def __init__(self, **layers):
		super(Encoder, self).__init__(**layers)
		self.activation_function = "tanh"
		self.output_activation_function = None
		self.apply_batchnorm_to_input = True
		self.apply_batchnorm = True
		self.apply_dropout = True

	@property
	def xp(self):
		return np if self._cpu else cuda.cupy

	def forward_one_step(self, x, test=False, sample_output=True):
		f = activations[self.activation_function]

		chain_mean = [x]
		chain_variance = [x]

		# Hidden
		for i in range(self.n_layers):
			u = chain_mean[-1]
			if i == 0:
				if self.apply_batchnorm_to_input:
					u = getattr(self, "batchnorm_mean_%d" % i)(u, test=test)
			else:
				if self.apply_batchnorm:
					u = getattr(self, "batchnorm_mean_%d" % i)(u, test=test)
			u = getattr(self, "layer_mean_%i" % i)(u)
			if i == self.n_layers - 1:
				if self.output_activation_function is None:
					output = u
				else:
					output = activations[self.output_activation_function](u)
			else:
				output = f(u)
				if self.apply_dropout:
					output = F.dropout(output, train=not test)
			chain_mean.append(output)

			u = chain_variance[-1]
			if i == 0:
				if self.apply_batchnorm_to_input:
					u = getattr(self, "batchnorm_var_%i" % i)(u, test=test)
			else:
				if self.apply_batchnorm:
					u = getattr(self, "batchnorm_var_%i" % i)(u, test=test)
			u = getattr(self, "layer_var_%i" % i)(u)
			if i == self.n_layers - 1:
				if self.output_activation_function is None:
					output = u
				else:
					output = activations[self.output_activation_function](u)
			else:
				output = f(u)
				if self.apply_dropout:
					output = F.dropout(output, train=not test)
			chain_variance.append(output)

		mean = chain_mean[-1]
		# log(sigma^2)
		ln_var = chain_variance[-1]

		return mean, ln_var

	def __call__(self, x, test=False, sample_output=True):
		mean, ln_var = self.forward_one_step(x, test=test, sample_output=sample_output)
		if sample_output:
			return F.gaussian(mean, ln_var)
		return mean, ln_var

# Network structure is same as the Encoder
class GaussianDecoder(Encoder):

	def __call__(self, x, test=False, output_pixel_value=False):
		mean, ln_var = self.forward_one_step(x, test=test, sample_output=False)
		if output_pixel_value:
			return F.gaussian(mean, ln_var)
		return mean, ln_var

class BernoulliDecoder(chainer.Chain):
	def __init__(self, **layers):
		super(BernoulliDecoder, self).__init__(**layers)
		self.activation_function = "tanh"
		self.output_activation_function = None
		self.apply_batchnorm_to_input = True
		self.apply_batchnorm = True
		self.apply_dropout = True

	@property
	def xp(self):
		return np if self._cpu else cuda.cupy

	def forward_one_step(self, x, test=False, output_pixel_value=False):
		f = activations[self.activation_function]
		chain = [x]

		# Hidden
		for i in range(self.n_layers):
			u = chain[-1]
			if i == 0:
				if self.apply_batchnorm_to_input:
					u = getattr(self, "batchnorm_%d" % i)(u, test=test)
			else:
				if self.apply_batchnorm:
					u = getattr(self, "batchnorm_%d" % i)(u, test=test)
			u = getattr(self, "layer_%i" % i)(u)
			if i == self.n_layers - 1:
				output = u
			else:
				output = f(u)
				if self.apply_dropout:
					output = F.dropout(output, train=not test)
			chain.append(output)

		# Pixel value must be between -1 to 1
		if output_pixel_value:
			return (F.sigmoid(chain[-1]) - 0.5) * 2.0

		return chain[-1]

	def __call__(self, x, test=False, output_pixel_value=False):
		return self.forward_one_step(x, test=test, output_pixel_value=output_pixel_value)